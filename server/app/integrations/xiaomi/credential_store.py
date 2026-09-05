from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.fernet import Fernet


@dataclass
class XiaomiCredentials:
    mi_user_id: str
    pass_token: str
    last_sync_at: int = 0
    available_data_types: list[str] = field(default_factory=list)


class CredentialStore:
    """Fernet-encrypted per-Auri-user Xiaomi credentials."""

    def __init__(self, root: Path, secret_key: str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._fernet = Fernet(secret_key.encode())

    def _path(self, user_id: str) -> Path:
        digest = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
        return self.root / f"{digest}.json"

    def exists(self, user_id: str) -> bool:
        return self._path(user_id).exists()

    def save(self, user_id: str, mi_user_id: str, pass_token: str) -> None:
        payload = {
            "mi_user_id": self._fernet.encrypt(mi_user_id.encode()).decode(),
            "pass_token": self._fernet.encrypt(pass_token.encode()).decode(),
            "last_sync_at": 0,
            "available_data_types": [],
        }
        self._write(user_id, payload)

    def load(self, user_id: str) -> XiaomiCredentials | None:
        path = self._path(user_id)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return XiaomiCredentials(
                mi_user_id=self._fernet.decrypt(payload["mi_user_id"].encode()).decode(),
                pass_token=self._fernet.decrypt(payload["pass_token"].encode()).decode(),
                last_sync_at=int(payload.get("last_sync_at", 0)),
                available_data_types=list(payload.get("available_data_types", [])),
            )
        except (KeyError, ValueError, TypeError, json.JSONDecodeError, OSError):
            return None

    def update_status(
        self,
        user_id: str,
        last_sync_at: int,
        available_data_types: list[str],
    ) -> None:
        credentials = self.load(user_id)
        if credentials is None:
            return
        payload = {
            "mi_user_id": self._fernet.encrypt(credentials.mi_user_id.encode()).decode(),
            "pass_token": self._fernet.encrypt(credentials.pass_token.encode()).decode(),
            "last_sync_at": last_sync_at,
            "available_data_types": available_data_types,
        }
        self._write(user_id, payload)

    def delete(self, user_id: str) -> None:
        path = self._path(user_id)
        if path.exists():
            path.unlink()

    def _write(self, user_id: str, payload: dict) -> None:
        path = self._path(user_id)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(path)


def resolve_secret_key(secret_key: str | None, data_dir: Path) -> str:
    """Return a valid Fernet key, generating and persisting one when unset."""

    if secret_key:
        return secret_key

    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    key_path = data_dir / "secret.key"
    if key_path.exists():
        return key_path.read_text(encoding="utf-8").strip()

    generated = Fernet.generate_key().decode()
    key_path.write_text(generated, encoding="utf-8")
    return generated
