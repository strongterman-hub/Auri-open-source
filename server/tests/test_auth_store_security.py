from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from app.auth.store import AuthStore, PASSWORD_ALGORITHM


def test_new_passwords_and_tokens_are_not_stored_as_fast_or_plain_secrets(tmp_dir) -> None:
    store = AuthStore(tmp_dir / "auth")
    user = store.create_user("owner@example.com", "correct-horse")
    token = store.create_token(user.id)

    users = json.loads((tmp_dir / "auth" / "users.json").read_text(encoding="utf-8"))
    tokens = json.loads((tmp_dir / "auth" / "tokens.json").read_text(encoding="utf-8"))

    assert users[user.id]["password_algorithm"] == PASSWORD_ALGORITHM
    assert users[user.id]["password_hash"] != hashlib.sha256(
        (users[user.id]["salt"] + "correct-horse").encode("utf-8")
    ).hexdigest()
    assert token not in tokens
    assert store.resolve_user_id(token) == user.id


def test_legacy_sha256_password_is_upgraded_after_successful_login(tmp_dir) -> None:
    auth_dir = tmp_dir / "auth"
    auth_dir.mkdir()
    salt = "0123456789abcdef0123456789abcdef"
    password = "legacy-password"
    auth_dir.joinpath("users.json").write_text(
        json.dumps(
            {
                "owner@example.com": {
                    "id": "owner@example.com",
                    "email": "owner@example.com",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "password_hash": hashlib.sha256(
                        (salt + password).encode("utf-8")
                    ).hexdigest(),
                    "salt": salt,
                }
            }
        ),
        encoding="utf-8",
    )
    store = AuthStore(auth_dir)

    assert store.verify_user("owner@example.com", password) is not None
    migrated = json.loads(auth_dir.joinpath("users.json").read_text(encoding="utf-8"))
    assert migrated["owner@example.com"]["password_algorithm"] == PASSWORD_ALGORITHM
    assert store.verify_user("owner@example.com", "wrong") is None
