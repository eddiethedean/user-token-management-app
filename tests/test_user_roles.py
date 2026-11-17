"""Promotion transactions and races against real SQLite and PostgreSQL sessions."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Barrier, Event

import pytest
from fastapi import Request
from sqlalchemy import URL, create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.database import Base
from app.dependencies import get_optional_auth
from app.models import AuditEvent, RefreshSession, Role, User, utcnow
from app.services import sessions as session_service
from app.services import user_roles
from app.services.auth_common import AuthenticationError, TokenFlowError, ensure_default_roles
from app.services.user_roles import PromotionError, PromotionRejected, PromotionResult, promote_user

_WAIT = 8


@pytest.fixture()
def postgres_role_sessions(postgresql):
    dsn = postgresql.dsn()
    engine = create_engine(
        URL.create(
            "postgresql+psycopg",
            username=dsn["user"],
            host=dsn["host"],
            port=dsn["port"],
            database=dsn["database"],
        ),
        connect_args={"options": "-c statement_timeout=7000 -c lock_timeout=6000"},
    )
    Base.metadata.create_all(engine)
    try:
        yield sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    finally:
        engine.dispose()


@pytest.fixture()
def sqlite_role_sessions(access_app):
    # The app fixture migrates an isolated file-backed database.
    return access_app.state.execution.sessions


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)])
def role_sessions(request):
    return request.getfixturevalue(f"{request.param}_role_sessions")


@dataclass(frozen=True)
class Accounts:
    actor: str
    target: str


def _seed(sessions: sessionmaker[Session]) -> Accounts:
    with sessions() as db:
        ensure_default_roles(db)
        ids: list[str] = []
        for label, role_name in (("actor", "administrator"), ("target", "user")):
            role = db.scalars(select(Role).where(Role.name == role_name)).one()
            user = User(
                email=f"promotion.{label}@example.gov",
                email_original=f"promotion.{label}@example.gov",
                status="active",
                email_verified_at=utcnow(),
                password_hash="fixture-password-hash",
                roles=[role],
            )
            db.add(user)
            db.flush()
            ids.append(user.id)
        db.commit()
    return Accounts(*ids)


def _promote(db: Session, accounts: Accounts) -> PromotionResult:
    return promote_user(
        db, actor_id=accounts.actor, target_id=accounts.target, authentication_mode="local_password"
    )


def _events(db: Session) -> list[AuditEvent]:
    return list(
        db.scalars(select(AuditEvent).where(AuditEvent.event_type == "admin.user.roles_changed"))
    )


def _session(db: Session, user_id: str):
    user = db.get(User, user_id)
    assert user is not None
    return session_service.create_session(db, get_settings(), user)


def _assert_revoked(sessions: sessionmaker[Session], session_id: str, raw_refresh: str) -> None:
    with sessions() as db:
        stored = db.get(RefreshSession, session_id)
        assert stored is not None and stored.revoked_at is not None
        with pytest.raises(TokenFlowError):
            session_service.rotate_session(db, get_settings(), raw_refresh)


def test_audit_failure_rolls_back_roles_version_and_revocation(
    sqlite_role_sessions, monkeypatch
) -> None:
    accounts = _seed(sqlite_role_sessions)
    with sqlite_role_sessions() as db:
        session_id = _session(db, accounts.target).session.id

    audit = user_roles.record_event

    def fail_after_write(db, *args, **kwargs):
        audit(db, *args, **kwargs)
        db.flush()
        raise RuntimeError("forced audit transaction failure")

    monkeypatch.setattr(user_roles, "record_event", fail_after_write)
    with sqlite_role_sessions() as db:
        with pytest.raises(RuntimeError, match="forced audit transaction failure"):
            _promote(db, accounts)
        assert not db.in_transaction()
    with sqlite_role_sessions() as db:
        target = db.get(User, accounts.target)
        session = db.get(RefreshSession, session_id)
        assert target is not None and target.role_names == ["user"] and target.security_version == 1
        assert session is not None and session.revoked_at is None
        assert _events(db) == []


@pytest.mark.parametrize("change", ["disable", "remove_role", "delete_role"])
def test_promotion_reloads_actor_and_role_after_a_stale_read(sqlite_role_sessions, change) -> None:
    accounts = _seed(sqlite_role_sessions)
    with sqlite_role_sessions() as db:
        actor = db.get(User, accounts.actor)
        assert actor is not None and actor.role_names == ["administrator"]
        with sqlite_role_sessions() as writer:
            current = writer.get(User, accounts.actor)
            assert current is not None
            if change == "disable":
                current.status = "disabled"
            elif change == "remove_role":
                current.roles.clear()
            else:
                writer.delete(current.roles[0])
            writer.commit()

        with pytest.raises(PromotionRejected) as rejected:
            _promote(db, accounts)
        assert rejected.value.code is (
            PromotionError.ROLE_UNAVAILABLE if change == "delete_role" else PromotionError.FORBIDDEN
        )
        assert not db.in_transaction()
        assert _events(db) == []


def test_self_promotion_of_an_administrator_is_a_noop(sqlite_role_sessions) -> None:
    accounts = _seed(sqlite_role_sessions)
    with sqlite_role_sessions() as db:
        created = _session(db, accounts.actor)
        result = _promote(db, Accounts(accounts.actor, accounts.actor))
        assert result is PromotionResult.ALREADY_ADMINISTRATOR
        assert not db.in_transaction()
        actor = db.get(User, accounts.actor)
        assert actor is not None and actor.security_version == 1
        assert created.session.revoked_at is None
        assert _events(db) == []


def test_sqlite_busy_snapshot_rolls_back_and_can_retry(sqlite_role_sessions) -> None:
    accounts = _seed(sqlite_role_sessions)
    with sqlite_role_sessions() as db:
        db.connection().exec_driver_sql("PRAGMA journal_mode=WAL")
        db.commit()
        # Begin a real SQLite read transaction, then commit a write on a separate connection.
        db.connection().exec_driver_sql("BEGIN")
        assert db.get(User, accounts.target) is not None
        with sqlite_role_sessions() as writer:
            target = writer.get(User, accounts.target)
            assert target is not None
            target.full_name = "Concurrent profile update"
            writer.commit()

        with pytest.raises(PromotionRejected) as rejected:
            _promote(db, accounts)
        assert rejected.value.code is PromotionError.WRITE_CONFLICT
        assert not db.in_transaction()
        assert _events(db) == []
        assert _promote(db, accounts) is PromotionResult.PROMOTED
        assert db.get(User, accounts.target).full_name == "Concurrent profile update"


def test_write_lock_conflict_is_retryable_without_partial_changes(role_sessions) -> None:
    accounts = _seed(role_sessions)
    with role_sessions() as holder, role_sessions() as contender:
        assert user_roles.lock_administrator_role(holder) is not None
        connection = contender.connection()
        if connection.dialect.name == "sqlite":
            connection.exec_driver_sql("PRAGMA busy_timeout=50")
        else:
            connection.exec_driver_sql("SET LOCAL lock_timeout = '50ms'")
        with pytest.raises(PromotionRejected) as rejected:
            _promote(contender, accounts)
        assert rejected.value.code is PromotionError.WRITE_CONFLICT
        assert not contender.in_transaction()
        holder.rollback()
        assert _events(contender) == []
        assert _promote(contender, accounts) is PromotionResult.PROMOTED


def test_concurrent_promotions_both_read_old_roles_and_commit_only_once(role_sessions) -> None:
    accounts = _seed(role_sessions)
    with role_sessions() as db:
        old = _session(db, accounts.target)
        session_id, refresh = old.session.id, old.refresh_token
    both_read = Barrier(2)

    def apply_promotion() -> PromotionResult:
        with role_sessions() as db:
            target = db.get(User, accounts.target)
            assert target is not None and target.role_names == ["user"]
            both_read.wait(_WAIT)
            return _promote(db, accounts)

    with ThreadPoolExecutor(2) as pool:
        operations = [pool.submit(apply_promotion) for _ in range(2)]
        assert {job.result(_WAIT) for job in operations} == {
            PromotionResult.PROMOTED,
            PromotionResult.ALREADY_ADMINISTRATOR,
        }
    with role_sessions() as db:
        target = db.get(User, accounts.target)
        assert target is not None and target.role_names == ["administrator", "user"]
        assert target.security_version == 2
        assert len(_events(db)) == 1
    _assert_revoked(role_sessions, session_id, refresh)


def test_session_creation_with_old_verified_version_loses_to_promotion(role_sessions) -> None:
    accounts = _seed(role_sessions)
    with role_sessions() as signing_in:
        user = signing_in.get(User, accounts.target)
        assert user is not None
        old_version = user.security_version
        with role_sessions() as writer:
            assert _promote(writer, accounts) is PromotionResult.PROMOTED
        with pytest.raises(AuthenticationError):
            session_service.create_session(
                signing_in, get_settings(), user, expected_security_version=old_version
            )
    with role_sessions() as db:
        assert (
            db.scalar(select(RefreshSession.id).where(RefreshSession.user_id == accounts.target))
            is None
        )
        assert _session(db, accounts.target).security_version == old_version + 1


@pytest.mark.postgres
@pytest.mark.parametrize("refreshing_account", ["actor", "target"])
def test_postgres_refresh_audit_can_finish_while_promotion_holds_user_locks(
    postgres_role_sessions, monkeypatch, refreshing_account
) -> None:
    accounts = _seed(postgres_role_sessions)
    settings = get_settings()
    with postgres_role_sessions() as db:
        tokens = _session(db, getattr(accounts, refreshing_account))
        session_id = tokens.session.id

    refresh_holds_session = Event()
    promotion_holds_users = Event()
    refresh_committed = Event()
    original_audit = session_service.record_event
    original_revoke = user_roles.revoke_all_sessions
    lock_statements: list[str] = []

    def capture_lock(_conn, _cursor, statement, _parameters, _context, _executemany):
        if "FOR " in statement and "FROM users" in statement:
            lock_statements.append(statement)

    def audit_after_user_locks(db, event_type, **kwargs):
        if event_type == "auth.session.refreshed":
            refresh_holds_session.set()
            assert promotion_holds_users.wait(_WAIT)
        return original_audit(db, event_type, **kwargs)

    def revoke_after_refresh_lock(db, target):
        promotion_holds_users.set()
        if refreshing_account == "actor":
            # The actor's refresh must be able to commit before promotion releases its user lock.
            assert refresh_committed.wait(_WAIT)
        return original_revoke(db, target)

    def rotate():
        with postgres_role_sessions() as db:
            result = session_service.rotate_session(db, settings, tokens.refresh_token)
            refresh_committed.set()
            return result

    def promote():
        assert refresh_holds_session.wait(_WAIT)
        with postgres_role_sessions() as db:
            return _promote(db, accounts)

    monkeypatch.setattr(session_service, "record_event", audit_after_user_locks)
    monkeypatch.setattr(user_roles, "revoke_all_sessions", revoke_after_refresh_lock)
    engine = postgres_role_sessions.kw["bind"]
    event.listen(engine, "before_cursor_execute", capture_lock)
    try:
        with ThreadPoolExecutor(2) as pool:
            refreshing = pool.submit(rotate)
            promoting = pool.submit(promote)
            assert promoting.result(_WAIT + 2) is PromotionResult.PROMOTED
            renewed = refreshing.result(_WAIT + 2)
    finally:
        event.remove(engine, "before_cursor_execute", capture_lock)

    assert len(lock_statements) == 1
    assert "ORDER BY users.id FOR NO KEY UPDATE OF users" in lock_statements[0]
    if refreshing_account == "target":
        _assert_revoked(postgres_role_sessions, session_id, renewed.refresh_token)
        request = Request(
            {
                "type": "http",
                "headers": [(b"authorization", f"Bearer {renewed.access_token}".encode())],
            }
        )
        with postgres_role_sessions() as db:
            assert get_optional_auth(request, db, settings) is None
    else:
        with postgres_role_sessions() as db:
            session = db.get(RefreshSession, session_id)
            assert session is not None and session.revoked_at is None
            assert (
                session_service.rotate_session(db, settings, renewed.refresh_token).session.id
                == session_id
            )


def test_promotion_can_revoke_before_an_overlapping_target_refresh(
    role_sessions, monkeypatch
) -> None:
    accounts = _seed(role_sessions)
    settings = get_settings()
    with role_sessions() as db:
        original = _session(db, accounts.target)
    session_revoked_in_transaction = Event()
    refresh_entering_update = Event()
    revoke_sessions = user_roles.revoke_all_sessions
    issue_update = session_service.scalar_returning

    def pause_after_revocation(db, target):
        revoke_sessions(db, target)
        session_revoked_in_transaction.set()
        assert refresh_entering_update.wait(_WAIT)

    def mark_refresh_attempt(*args, **kwargs):
        refresh_entering_update.set()
        return issue_update(*args, **kwargs)

    def promote():
        with role_sessions() as db:
            return _promote(db, accounts)

    def refresh():
        assert session_revoked_in_transaction.wait(_WAIT)
        with role_sessions() as db, pytest.raises(TokenFlowError):
            session_service.rotate_session(db, settings, original.refresh_token)

    monkeypatch.setattr(user_roles, "revoke_all_sessions", pause_after_revocation)
    monkeypatch.setattr(session_service, "scalar_returning", mark_refresh_attempt)
    with ThreadPoolExecutor(2) as pool:
        promotion = pool.submit(promote)
        rotation = pool.submit(refresh)
        assert promotion.result(_WAIT + 2) is PromotionResult.PROMOTED
        rotation.result(_WAIT + 2)


@pytest.mark.parametrize("winner", ["disable", "promote"])
def test_promotion_and_existing_disable_action_serialize(
    role_sessions, access_app, client, monkeypatch, winner
) -> None:
    from app.ui.routes import admin as admin_routes

    accounts = _seed(role_sessions)
    with role_sessions() as db:
        actor_tokens = _session(db, accounts.actor)
        target_session_id = _session(db, accounts.target).session.id
        headers = {
            "Authorization": f"Bearer {actor_tokens.access_token}",
            "X-CSRF-Token": actor_tokens.session.csrf_token,
        }

    monkeypatch.setattr(access_app.state.execution, "sessions", role_sessions)
    winner_locked = Event()
    competing = Event()
    disable_lock = admin_routes.lock_administrator_action
    promotion_role_lock = user_roles.lock_administrator_role
    revoke = user_roles.revoke_all_sessions

    def lock_for_disable(db, actor):
        if winner == "promote":
            competing.set()
        locked = disable_lock(db, actor)
        if winner == "disable":
            winner_locked.set()
            assert competing.wait(_WAIT)
        return locked

    def lock_for_promotion(db):
        if winner == "disable":
            competing.set()
        return promotion_role_lock(db)

    def revoke_for_promotion(db, target):
        if winner == "promote":
            winner_locked.set()
            assert competing.wait(_WAIT)
        return revoke(db, target)

    def disable():
        if winner == "promote":
            assert winner_locked.wait(_WAIT)
        return client.post(f"/admin/users/{accounts.target}/toggle", headers=headers)

    def promote():
        if winner == "disable":
            assert winner_locked.wait(_WAIT)
        with role_sessions() as db:
            return _promote(db, accounts)

    monkeypatch.setattr(admin_routes, "lock_administrator_action", lock_for_disable)
    monkeypatch.setattr(user_roles, "lock_administrator_role", lock_for_promotion)
    monkeypatch.setattr(user_roles, "revoke_all_sessions", revoke_for_promotion)
    with ThreadPoolExecutor(2) as pool:
        disabled = pool.submit(disable)
        promoted = pool.submit(promote)
        response = disabled.result(_WAIT + 2)
        assert response.status_code == 303, response.text
        if winner == "disable":
            with pytest.raises(PromotionRejected) as rejected:
                promoted.result(_WAIT + 2)
            assert rejected.value.code is PromotionError.INELIGIBLE
        else:
            assert promoted.result(_WAIT + 2) is PromotionResult.PROMOTED

    with role_sessions() as db:
        target = db.get(User, accounts.target)
        target_session = db.get(RefreshSession, target_session_id)
        assert target is not None and target.status == "disabled"
        assert target_session is not None and target_session.revoked_at is not None
        if winner == "disable":
            assert target.role_names == ["user"] and target.security_version == 2
            assert _events(db) == []
        else:
            assert target.role_names == ["administrator", "user"] and target.security_version == 3
            assert len(_events(db)) == 1
