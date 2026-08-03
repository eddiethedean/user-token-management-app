import argparse
import getpass
import os
import sys

from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.models import Role, User, UserStatus, utcnow
from app.schema import assert_schema_current, current_revision, head_revision, upgrade_schema
from app.security.email import normalize_email
from app.security.passwords import PasswordPolicyError, PasswordService, validate_password
from app.server import run_server
from app.services.auth import ensure_default_roles
from app.services.mailer import deliver_pending


def create_admin(email: str, password: str | None = None) -> int:
    settings = get_settings()
    assert_schema_current()
    with SessionLocal() as db:
        ensure_default_roles(db)
        canonical, original = normalize_email(email, settings)
        existing = db.scalar(select(User).where(User.email == canonical))
        if password is None:
            password = getpass.getpass("Password: ")
            confirmation = getpass.getpass("Confirm password: ")
            if password != confirmation:
                print("Passwords do not match.", file=sys.stderr)
                return 2
        try:
            validated = validate_password(password, email=canonical)
        except PasswordPolicyError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        role = db.scalar(select(Role).where(Role.name == "administrator"))
        if existing:
            existing.password_hash = PasswordService(settings).hash(validated)
            existing.password_changed_at = utcnow()
            existing.email_verified_at = existing.email_verified_at or utcnow()
            existing.status = UserStatus.ACTIVE.value
            if role and role not in existing.roles:
                existing.roles.append(role)
            user = existing
        else:
            user = User(
                email=canonical,
                email_original=original,
                email_verified_at=utcnow(),
                status=UserStatus.ACTIVE.value,
                password_hash=PasswordService(settings).hash(validated),
                password_changed_at=utcnow(),
                roles=[role] if role else [],
            )
            db.add(user)
        db.commit()
        print(f"Administrator ready: {user.email_original}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="utm")
    subparsers = parser.add_subparsers(dest="command", required=True)
    admin_parser = subparsers.add_parser("create-admin", help="Create or promote an administrator")
    admin_parser.add_argument("--email", required=True)
    admin_parser.add_argument(
        "--password-env",
        help="Read the password from this environment variable instead of an interactive prompt",
    )
    migrate_parser = subparsers.add_parser("migrate", help="Upgrade the database schema to head")
    migrate_parser.add_argument(
        "--adopt-existing",
        action="store_true",
        help="Stamp a verified pre-Alembic Access Registry schema before upgrading",
    )
    subparsers.add_parser("schema-status", help="Show the current and expected schema revisions")
    subparsers.add_parser("send-email", help="Deliver queued email")
    serve_parser = subparsers.add_parser(
        "serve", help="Run locally or through the Posit Workbench proxy"
    )
    serve_parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    serve_parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    serve_parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    if args.command == "create-admin":
        password = None
        if args.password_env:
            password = os.environ.get(args.password_env)
            if password is None:
                print(f"Environment variable {args.password_env!r} is not set.", file=sys.stderr)
                raise SystemExit(2)
        raise SystemExit(create_admin(args.email, password=password))
    if args.command == "migrate":
        upgrade_schema(adopt_existing=args.adopt_existing)
        print(f"Database schema upgraded to {head_revision()}.")
    if args.command == "schema-status":
        print(f"Current: {current_revision() or 'none'}")
        print(f"Head:    {head_revision()}")
    if args.command == "send-email":
        assert_schema_current()
        with SessionLocal() as db:
            delivered = deliver_pending(db, get_settings())
            print(f"Delivered {delivered} message(s).")
    if args.command == "serve":
        run_server(host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
