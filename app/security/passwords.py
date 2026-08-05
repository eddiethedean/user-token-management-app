from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from pwdlib import PasswordHash
from pwdlib.exceptions import PwdlibError

from app.config import Settings

COMMON_PASSWORDS = {
    "123456789012345",
    "correcthorsebatterystaple",
    "letmeinletmeinletmein",
    "passwordpassword",
    "qwertyqwertyqwerty",
    "welcome123456789",
}
DUMMY_PASSWORD = "constant-time-password-verification-value"


class PasswordPolicyError(ValueError):
    pass


def normalize_password(password: str) -> str:
    return unicodedata.normalize("NFC", password)


@lru_cache(maxsize=8)
def load_password_blocklist(path: str) -> frozenset[str]:
    if not path:
        return frozenset()
    values = {
        normalize_password(line.strip()).casefold()
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    return frozenset(values)


def validate_password(password: str, *, email: str = "", blocklist_path: str = "") -> str:
    normalized = normalize_password(password)
    if len(normalized) < 15:
        raise PasswordPolicyError("Use at least 15 characters.")
    if len(normalized) > 128:
        raise PasswordPolicyError("Use no more than 128 characters.")
    lowered = normalized.casefold()
    local_part = email.partition("@")[0].casefold()
    configured_blocklist = load_password_blocklist(blocklist_path)
    if (
        lowered in COMMON_PASSWORDS
        or lowered in configured_blocklist
        or (local_part and local_part in lowered)
    ):
        raise PasswordPolicyError("Choose a password that is not common or based on your email.")
    return normalized


@dataclass
class PasswordService:
    settings: Settings

    def __post_init__(self) -> None:
        self._argon2 = PasswordHash.recommended()

    def hash(self, password: str) -> str:
        password = normalize_password(password)
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
        password = normalize_password(password)
        if len(password) > 128:
            self.hash(DUMMY_PASSWORD)
            return False
        if not password_hash:
            self.hash(DUMMY_PASSWORD)
            return False
        if password_hash.startswith("$argon2"):
            try:
                return self._argon2.verify(password, password_hash)
            except PwdlibError:
                return False
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
            return not password_hash.startswith(
                "$argon2"
            ) or self._argon2.current_hasher.check_needs_rehash(password_hash)
        if not password_hash.startswith("pbkdf2_sha256$"):
            return True
        try:
            return int(password_hash.split("$", 2)[1]) < self.settings.pbkdf2_iterations
        except (ValueError, IndexError):
            return True


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)
