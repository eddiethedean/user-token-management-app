import base64
import hashlib
import hmac
import secrets
import unicodedata
from dataclasses import dataclass

from pwdlib import PasswordHash

from app.config import Settings


COMMON_PASSWORDS = {
    "123456789012345",
    "correcthorsebatterystaple",
    "letmeinletmeinletmein",
    "passwordpassword",
    "qwertyqwertyqwerty",
    "welcome123456789",
}


class PasswordPolicyError(ValueError):
    pass


def validate_password(password: str, *, email: str = "") -> str:
    normalized = unicodedata.normalize("NFC", password)
    if len(normalized) < 15:
        raise PasswordPolicyError("Use at least 15 characters.")
    if len(normalized) > 128:
        raise PasswordPolicyError("Use no more than 128 characters.")
    lowered = normalized.casefold()
    local_part = email.partition("@")[0].casefold()
    if lowered in COMMON_PASSWORDS or (local_part and local_part in lowered):
        raise PasswordPolicyError("Choose a password that is not common or based on your email.")
    return normalized


@dataclass
class PasswordService:
    settings: Settings

    def __post_init__(self) -> None:
        self._argon2 = PasswordHash.recommended()

    def hash(self, password: str) -> str:
        if self.settings.password_hash_scheme == "argon2":
            return self._argon2.hash(password)
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, self.settings.pbkdf2_iterations
        )
        return "$".join(
            (
                "pbkdf2_sha256",
                str(self.settings.pbkdf2_iterations),
                base64.urlsafe_b64encode(salt).decode("ascii").rstrip("="),
                base64.urlsafe_b64encode(digest).decode("ascii").rstrip("="),
            )
        )

    def verify(self, password: str, password_hash: str | None) -> bool:
        if not password_hash:
            self._argon2.hash(password)
            return False
        if password_hash.startswith("$argon2"):
            return self._argon2.verify(password, password_hash)
        if password_hash.startswith("pbkdf2_sha256$"):
            try:
                _, iterations_text, salt_text, digest_text = password_hash.split("$", 3)
                salt = _b64decode(salt_text)
                expected = _b64decode(digest_text)
                actual = hashlib.pbkdf2_hmac(
                    "sha256", password.encode("utf-8"), salt, int(iterations_text)
                )
                return hmac.compare_digest(actual, expected)
            except (ValueError, TypeError):
                return False
        return False

    def needs_rehash(self, password_hash: str) -> bool:
        if self.settings.password_hash_scheme == "argon2":
            return not password_hash.startswith("$argon2") or self._argon2.check_needs_rehash(
                password_hash
            )
        if not password_hash.startswith("pbkdf2_sha256$"):
            return True
        try:
            return int(password_hash.split("$", 2)[1]) < self.settings.pbkdf2_iterations
        except (ValueError, IndexError):
            return True


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)

