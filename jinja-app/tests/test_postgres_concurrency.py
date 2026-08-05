import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Lock

import pytest
from sqlalchemy import create_engine, delete, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.database import Base
from app.models import (
    ApiTokenKeyUsage,
    EmailOutbox,
    Invitation,
    PasswordReset,
    RefreshSession,
    RefreshTokenHistory,
    RegistrationVerification,
    Role,
    User,
    UserSecret,
    UserStatus,
    utcnow,
)
from app.security.passwords import PasswordService
from app.security.tokens import hash_token, random_token
from app.services.accounts import CurrentPasswordError, change_password
from app.services.auth import (
    AuthenticationError,
    TokenFlowError,
    accept_invitation,
    authenticate_user,
    complete_password_reset,
    create_invitation,
    create_session,
    ensure_default_roles,
    lock_administrator_action,
    request_password_reset,
    request_self_registration,
    rotate_session,
)
from app.services.mailer import deliver_pending_with_metrics, queue_email
from app.services.secrets import store_user_secret

pytestmark = pytest.mark.postgres
POSTGRES_TEST_DATABASE_URL = os.environ.get("POSTGRES_TEST_DATABASE_URL", "")


@pytest.fixture(scope="module")
def postgres_sessions():
    if not POSTGRES_TEST_DATABASE_URL:
        pytest.skip("Set POSTGRES_TEST_DATABASE_URL to run PostgreSQL concurrency tests")
    pytest.importorskip("psycopg")
    url = make_url(POSTGRES_TEST_DATABASE_URL)
    if not url.drivername.startswith("postgresql"):
        pytest.fail("POSTGRES_TEST_DATABASE_URL must use PostgreSQL")
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
    schema = f"access_registry_test_{uuid.uuid4().hex}"
    admin_engine = create_engine(url)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        ensure_default_roles(db)
    try:
        yield factory
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def _new_user(db: Session, email: str) -> User:
    role = db.scalar(select(Role).where(Role.name == "user"))
    settings = get_settings()
    user = User(
        email=email,
        email_original=email,
        email_verified_at=utcnow(),
        status=UserStatus.ACTIVE.value,
        password_hash=PasswordService(settings).hash("Initial-River-Password-71"),
        password_changed_at=utcnow(),
        roles=[role] if role else [],
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _race(factory, operation):
    barrier = Barrier(2)

    def invoke():
        barrier.wait()
        with factory() as db:
            try:
                return operation(db)
            except Exception as exc:  # Return both outcomes for exact assertions below.
                return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        return list(executor.map(lambda _: invoke(), range(2)))


def test_refresh_rotation_is_atomic_and_replay_revokes_family(postgres_sessions) -> None:
    settings = get_settings()
    with postgres_sessions() as db:
        user = _new_user(db, f"refresh-{uuid.uuid4().hex}@example.gov")
        tokens = create_session(db, settings, user)
        session_id = tokens.session.id

    outcomes = _race(
        postgres_sessions,
        lambda db: rotate_session(db, settings, tokens.refresh_token),
    )
    assert sum(not isinstance(item, Exception) for item in outcomes) == 1
    assert sum(isinstance(item, TokenFlowError) for item in outcomes) == 1
    with postgres_sessions() as db:
        session = db.get(RefreshSession, session_id)
        assert session is not None and session.revoked_at is not None
        assert (
            db.scalar(
                select(func.count())
                .select_from(RefreshTokenHistory)
                .where(RefreshTokenHistory.session_id == session_id)
            )
            == 1
        )


def test_invitation_acceptance_is_atomic(postgres_sessions) -> None:
    settings = get_settings()
    email = f"invite-{uuid.uuid4().hex}@example.gov"
    with postgres_sessions() as db:
        administrator = _new_user(db, f"admin-{uuid.uuid4().hex}@example.gov")
        admin_role = db.scalar(select(Role).where(Role.name == "administrator"))
        administrator.roles = [admin_role] if admin_role else []
        db.commit()
        invitation, raw_token = create_invitation(
            db,
            settings,
            email=email,
            role_name="user",
            inviter=administrator,
        )
        invitation_id = invitation.id

    outcomes = _race(
        postgres_sessions,
        lambda db: accept_invitation(
            db,
            settings,
            raw_token=raw_token,
            password="Accepted-Forest-Password-83",
            full_name="Concurrent Invitee",
        ),
    )
    assert sum(isinstance(item, User) for item in outcomes) == 1
    assert sum(isinstance(item, TokenFlowError) for item in outcomes) == 1
    with postgres_sessions() as db:
        assert db.scalar(select(func.count()).select_from(User).where(User.email == email)) == 1
        invitation = db.get(Invitation, invitation_id)
        assert invitation is not None and invitation.accepted_at is not None


def test_password_reset_consumption_is_atomic(postgres_sessions) -> None:
    settings = get_settings()
    first_password = "First-Reset-Password-81"
    second_password = "Second-Reset-Password-92"
    with postgres_sessions() as db:
        user = _new_user(db, f"reset-{uuid.uuid4().hex}@example.gov")
        user_id = user.id
        raw_token = random_token()
        reset = PasswordReset(
            user_id=user.id,
            token_hash=hash_token(raw_token, settings.session_pepper),
            created_at=utcnow(),
            expires_at=utcnow() + timedelta(hours=1),
        )
        db.add(reset)
        db.commit()

    passwords = iter((first_password, second_password))
    assigned = [next(passwords), next(passwords)]
    barrier = Barrier(2)

    def invoke(password):
        barrier.wait()
        with postgres_sessions() as db:
            try:
                return complete_password_reset(
                    db,
                    settings,
                    raw_token=raw_token,
                    password=password,
                )
            except Exception as exc:
                return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(invoke, assigned))
    assert sum(isinstance(item, User) for item in outcomes) == 1
    assert sum(isinstance(item, TokenFlowError) for item in outcomes) == 1
    with postgres_sessions() as db:
        user = db.get(User, user_id)
        assert user is not None
        verifier = PasswordService(settings)
        assert sum(verifier.verify(password, user.password_hash) for password in assigned) == 1


def test_failed_login_counter_is_atomic_and_terminal(postgres_sessions) -> None:
    settings = get_settings()
    email = f"lockout-{uuid.uuid4().hex}@example.gov"
    with postgres_sessions() as db:
        user = _new_user(db, email)
        user.failed_login_attempts = 4
        db.commit()
        user_id = user.id

    outcomes = _race(
        postgres_sessions,
        lambda db: authenticate_user(db, settings, email, "incorrect-password"),
    )
    assert all(isinstance(item, AuthenticationError) for item in outcomes)
    with postgres_sessions() as db:
        user = db.get(User, user_id)
        assert user is not None and user.failed_login_attempts == 5
        with pytest.raises(AuthenticationError):
            authenticate_user(
                db,
                settings,
                email,
                "Initial-River-Password-71",
            )


def test_concurrent_password_reset_requests_leave_one_live_capability(
    postgres_sessions,
) -> None:
    settings = get_settings()
    email = f"reset-request-{uuid.uuid4().hex}@example.gov"
    with postgres_sessions() as db:
        user_id = _new_user(db, email).id

    outcomes = _race(
        postgres_sessions,
        lambda db: request_password_reset(db, settings, email),
    )
    assert all(not isinstance(item, Exception) for item in outcomes)
    with postgres_sessions() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(PasswordReset)
                .where(PasswordReset.user_id == user_id, PasswordReset.used_at.is_(None))
            )
            == 1
        )


def test_concurrent_registration_requests_create_one_account_and_capability(
    postgres_sessions,
) -> None:
    settings = get_settings()
    email = f"registration-{uuid.uuid4().hex}@example.gov"
    outcomes = _race(
        postgres_sessions,
        lambda db: request_self_registration(
            db,
            settings,
            email=email,
            full_name="Concurrent Registrant",
        ),
    )
    assert all(not isinstance(item, Exception) for item in outcomes)
    with postgres_sessions() as db:
        user = db.scalar(select(User).where(User.email == email))
        assert user is not None
        assert (
            db.scalar(
                select(func.count())
                .select_from(RegistrationVerification)
                .where(
                    RegistrationVerification.user_id == user.id,
                    RegistrationVerification.used_at.is_(None),
                )
            )
            == 1
        )


def test_concurrent_invitation_creation_leaves_one_pending_invitation(
    postgres_sessions,
) -> None:
    settings = get_settings()
    email = f"invitation-create-{uuid.uuid4().hex}@example.gov"
    with postgres_sessions() as db:
        first_admin_id = _new_user(db, f"admin-one-{uuid.uuid4().hex}@example.gov").id
        second_admin_id = _new_user(db, f"admin-two-{uuid.uuid4().hex}@example.gov").id

    inviter_ids = iter((first_admin_id, second_admin_id))
    assigned_ids = [next(inviter_ids), next(inviter_ids)]
    barrier = Barrier(2)

    def invoke(inviter_id):
        barrier.wait()
        with postgres_sessions() as db:
            try:
                return create_invitation(
                    db,
                    settings,
                    email=email,
                    role_name="user",
                    inviter=db.get(User, inviter_id),
                )
            except Exception as exc:
                return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(invoke, assigned_ids))
    assert all(not isinstance(item, Exception) for item in outcomes)
    with postgres_sessions() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(Invitation)
                .where(
                    Invitation.email == email,
                    Invitation.accepted_at.is_(None),
                    Invitation.revoked_at.is_(None),
                )
            )
            == 1
        )


def test_concurrent_first_secret_write_upserts_one_record(postgres_sessions) -> None:
    settings = get_settings()
    with postgres_sessions() as db:
        user_id = _new_user(db, f"secret-{uuid.uuid4().hex}@example.gov").id
        initial_usage = db.get(ApiTokenKeyUsage, settings.api_token_active_key_id)
        initial_count = initial_usage.wrap_count if initial_usage else 0

    token_values = iter(("first-secret-token", "second-secret-token"))
    assigned_tokens = [next(token_values), next(token_values)]
    barrier = Barrier(2)

    def invoke(token):
        barrier.wait()
        with postgres_sessions() as db:
            try:
                return store_user_secret(
                    db,
                    settings,
                    user=db.get(User, user_id),
                    provider="advana",
                    token=token,
                )
            except Exception as exc:
                return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(invoke, assigned_tokens))
    assert all(isinstance(item, UserSecret) for item in outcomes), outcomes
    with postgres_sessions() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(UserSecret)
                .where(UserSecret.user_id == user_id, UserSecret.provider == "advana")
            )
            == 1
        )
        usage = db.get(ApiTokenKeyUsage, settings.api_token_active_key_id)
        assert usage is not None and usage.wrap_count == initial_count + 2


def test_concurrent_password_changes_have_one_winner(postgres_sessions) -> None:
    settings = get_settings()
    first_password = "First-Concurrent-Password-81"
    second_password = "Second-Concurrent-Password-92"
    with postgres_sessions() as db:
        user_id = _new_user(db, f"change-{uuid.uuid4().hex}@example.gov").id

    passwords = iter((first_password, second_password))
    assigned = [next(passwords), next(passwords)]
    barrier = Barrier(2)

    def invoke(new_password):
        barrier.wait()
        with postgres_sessions() as db:
            try:
                change_password(
                    db,
                    settings,
                    user=db.get(User, user_id),
                    current_password="Initial-River-Password-71",
                    new_password=new_password,
                )
                return True
            except Exception as exc:
                return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(invoke, assigned))
    assert outcomes.count(True) == 1
    assert sum(isinstance(item, CurrentPasswordError) for item in outcomes) == 1


def test_concurrent_administrator_disables_cannot_remove_every_active_admin(
    postgres_sessions,
) -> None:
    with postgres_sessions() as db:
        first_id = _new_user(db, f"admin-a-{uuid.uuid4().hex}@example.gov").id
        second_id = _new_user(db, f"admin-b-{uuid.uuid4().hex}@example.gov").id
        administrator = db.scalar(select(Role).where(Role.name == "administrator"))
        first = db.get(User, first_id)
        second = db.get(User, second_id)
        first.roles = [administrator]
        second.roles = [administrator]
        db.commit()

    pairs = iter(((first_id, second_id), (second_id, first_id)))
    assigned = [next(pairs), next(pairs)]
    barrier = Barrier(2)

    def invoke(pair):
        actor_id, target_id = pair
        barrier.wait()
        with postgres_sessions() as db:
            actor = db.get(User, actor_id)
            if not lock_administrator_action(db, actor):
                db.rollback()
                return False
            target = db.get(User, target_id)
            target.status = UserStatus.DISABLED.value
            target.security_version += 1
            db.commit()
            return True

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(invoke, assigned))
    assert sorted(outcomes) == [False, True]
    with postgres_sessions() as db:
        administrator = db.scalar(select(Role).where(Role.name == "administrator"))
        active_administrators = db.scalar(
            select(func.count())
            .select_from(User)
            .join(User.roles)
            .where(
                User.id.in_((first_id, second_id)),
                Role.id == administrator.id,
                User.status == UserStatus.ACTIVE.value,
            )
        )
        assert active_administrators == 1


def test_email_workers_claim_each_message_once(postgres_sessions, monkeypatch) -> None:
    settings = get_settings().model_copy(
        update={"email_backend": "smtp", "smtp_host": "relay.example.gov"}
    )
    sends = 0
    sends_lock = Lock()

    def record_send(message, worker_settings) -> None:
        nonlocal sends
        assert worker_settings.email_backend == "smtp"
        with sends_lock:
            sends += 1

    monkeypatch.setattr("app.services.mailer._send_smtp", record_send)
    with postgres_sessions() as db:
        # Earlier concurrency tests intentionally queue notices. Isolate this worker race
        # so each worker can only observe the message created for this assertion.
        db.execute(delete(EmailOutbox))
        db.commit()
        message = queue_email(db, "worker-race@example.gov", "Notice", "Body")
        db.commit()
        message_id = message.id

    outcomes = _race(
        postgres_sessions,
        lambda db: deliver_pending_with_metrics(db, settings, limit=1),
    )
    assert all(not isinstance(item, Exception) for item in outcomes)
    assert sum(item.claimed for item in outcomes) == 1
    assert sum(item.delivered for item in outcomes) == 1
    assert sends == 1
    with postgres_sessions() as db:
        message = db.get(EmailOutbox, message_id)
        assert message is not None and message.sent_at is not None
        assert message.attempts == 1
