from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.auth.models import AuthUser


def _hash_password(password: str, salt: str) -> str:
    """Legacy hash kept only for transparent migration on the next login."""
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()


PASSWORD_ALGORITHM = "pbkdf2_sha256"
PASSWORD_ITERATIONS = 310_000


def _hash_password_pbkdf2(password: str, salt: str, iterations: int) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt),
        iterations,
    ).hex()


def _token_key(token: str) -> str:
    return "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()


class AuthStore:
    """JSON-file-backed user and token store for a single-node deployment."""

    def __init__(self, root: Path, token_ttl_days: int = 30) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.token_ttl = timedelta(days=token_ttl_days)
        self._users_path = self.root / "users.json"
        self._tokens_path = self.root / "tokens.json"
        self._lock = threading.RLock()

    def _read_json(self, path: Path) -> dict:
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _write_json(self, path: Path, payload: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(path)

    def create_user(self, email: str, password: str) -> AuthUser:
        with self._lock:
            users = self._read_json(self._users_path)
            email_lower = email.lower()
            if email_lower in users:
                raise ValueError("该邮箱已注册")

            salt = secrets.token_hex(16)
            now = datetime.now(timezone.utc)
            user = AuthUser(
                id=email_lower,
                email=email_lower,
                created_at=now,
            )
            users[email_lower] = {
                "id": email_lower,
                "email": email_lower,
                "created_at": now.isoformat(),
                "password_hash": _hash_password_pbkdf2(
                    password,
                    salt,
                    PASSWORD_ITERATIONS,
                ),
                "password_algorithm": PASSWORD_ALGORITHM,
                "password_iterations": PASSWORD_ITERATIONS,
                "salt": salt,
            }
            self._write_json(self._users_path, users)
            return user

    def verify_user(self, email: str, password: str) -> AuthUser | None:
        with self._lock:
            users = self._read_json(self._users_path)
            email_lower = email.lower()
            record = users.get(email_lower)
            if not isinstance(record, dict):
                return None
            salt = str(record.get("salt", ""))
            expected = str(record.get("password_hash", ""))
            if record.get("password_algorithm") == PASSWORD_ALGORITHM:
                iterations = int(record.get("password_iterations", PASSWORD_ITERATIONS))
                candidate = _hash_password_pbkdf2(password, salt, iterations)
            else:
                candidate = _hash_password(password, salt)
            if not hmac.compare_digest(candidate, expected):
                return None

            if record.get("password_algorithm") != PASSWORD_ALGORITHM:
                upgraded = dict(record)
                upgraded_salt = secrets.token_hex(16)
                upgraded["salt"] = upgraded_salt
                upgraded["password_hash"] = _hash_password_pbkdf2(
                    password,
                    upgraded_salt,
                    PASSWORD_ITERATIONS,
                )
                upgraded["password_algorithm"] = PASSWORD_ALGORITHM
                upgraded["password_iterations"] = PASSWORD_ITERATIONS
                users[email_lower] = upgraded
                record = upgraded
                self._write_json(self._users_path, users)

            return AuthUser(
                id=record["id"],
                email=record["email"],
                created_at=datetime.fromisoformat(record["created_at"]),
            )

    def create_token(self, user_id: str) -> str:
        with self._lock:
            tokens = self._read_json(self._tokens_path)
            token = secrets.token_urlsafe(32)
            expires_at = datetime.now(timezone.utc) + self.token_ttl
            tokens[_token_key(token)] = {
                "user_id": user_id,
                "expires_at": expires_at.isoformat(),
            }
            self._write_json(self._tokens_path, tokens)
            return token

    def change_password_and_rotate_token(
        self,
        user_id: str,
        new_password: str,
    ) -> tuple[AuthUser, str]:
        with self._lock:
            users = self._read_json(self._users_path)
            record = users.get(user_id)
            if not isinstance(record, dict):
                raise ValueError("账号不存在")

            salt = secrets.token_hex(16)
            record = dict(record)
            record["salt"] = salt
            record["password_hash"] = _hash_password_pbkdf2(
                new_password,
                salt,
                PASSWORD_ITERATIONS,
            )
            record["password_algorithm"] = PASSWORD_ALGORITHM
            record["password_iterations"] = PASSWORD_ITERATIONS
            users[user_id] = record
            self._write_json(self._users_path, users)

            tokens = self._read_json(self._tokens_path)
            tokens = {
                token_key: token_record
                for token_key, token_record in tokens.items()
                if token_record.get("user_id") != user_id
            }
            token = secrets.token_urlsafe(32)
            tokens[_token_key(token)] = {
                "user_id": user_id,
                "expires_at": (datetime.now(timezone.utc) + self.token_ttl).isoformat(),
            }
            self._write_json(self._tokens_path, tokens)

            return (
                AuthUser(
                    id=record["id"],
                    email=record["email"],
                    created_at=datetime.fromisoformat(record["created_at"]),
                ),
                token,
            )

    def resolve_user_id(self, token: str) -> str | None:
        with self._lock:
            tokens = self._read_json(self._tokens_path)
            record = tokens.get(_token_key(token)) or tokens.get(token)
            if not record:
                return None
            try:
                expires_at = datetime.fromisoformat(record["expires_at"])
            except (KeyError, ValueError):
                return None
            if expires_at < datetime.now(timezone.utc):
                return None
            return record.get("user_id")

    def list_user_ids(self) -> list[str]:
        with self._lock:
            users = self._read_json(self._users_path)
            return list(users.keys())

    def user_exists(self, email: str) -> bool:
        with self._lock:
            users = self._read_json(self._users_path)
            return email.lower() in users

    def delete_user(self, user_id: str) -> bool:
        with self._lock:
            users = self._read_json(self._users_path)
            if user_id not in users:
                return False
            users.pop(user_id)
            self._write_json(self._users_path, users)
            return True

    def delete_tokens_for_user(self, user_id: str) -> None:
        with self._lock:
            tokens = self._read_json(self._tokens_path)
            tokens = {
                token: record
                for token, record in tokens.items()
                if record.get("user_id") != user_id
            }
            self._write_json(self._tokens_path, tokens)

    def revoke_token(self, token: str) -> None:
        with self._lock:
            tokens = self._read_json(self._tokens_path)
            tokens.pop(_token_key(token), None)
            tokens.pop(token, None)
            self._write_json(self._tokens_path, tokens)
