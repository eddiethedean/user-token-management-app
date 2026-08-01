import argparse
import getpass
import os
import sys

from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal, create_schema
from app.models import Role, User, UserStatus, utcnow
from app.security.email import normalize_email
from app.security.passwords import PasswordPolicyError, PasswordService, validate_password
from app.server import run_server
from app.services.auth import ensure_default_roles
from app.services.mailer import deliver_pending


def create_admin(email: str) -> int:
    settings = get_settings()
    create_schema()
    with SessionLocal() as db:
        ensure_default_roles(db)
        canonical, original = normalize_email(email, settings)
        existing = db.scalar(select(User).where(User.email == canonical))
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
    subparsers.add_parser("send-email", help="Deliver queued email")
    serve_parser = subparsers.add_parser(
        "serve", help="Run locally or through the Posit Workbench proxy"
    )
    serve_parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    serve_parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    serve_parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    if args.command == "create-admin":
        raise SystemExit(create_admin(args.email))
    if args.command == "send-email":
        create_schema()
        with SessionLocal() as db:
            delivered = deliver_pending(db, get_settings())
            print(f"Delivered {delivered} message(s).")
    if args.command == "serve":
        run_server(host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
