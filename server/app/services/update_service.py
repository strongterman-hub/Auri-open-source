from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class AppRelease:
    version_code: int = 0
    version_name: str = "0.0.0"
    force: bool = False
    changelog: str = ""
    download_url: str | None = None
    sha256: str | None = None
    apk_size: int = 0
    updated_at: float = 0.0

    def public_dict(self) -> dict[str, Any]:
        download_url = self.download_url or "/update/apk"
        if self.sha256 and download_url == "/update/apk":
            download_url = f"/update/apk/{self.sha256}"
        return {
            "version_code": self.version_code,
            "version_name": self.version_name,
            "force": self.force,
            "changelog": self.changelog,
            "download_url": download_url,
            "sha256": self.sha256,
            "apk_size": self.apk_size,
        }


class AppUpdateService:
    """File-backed store for the latest Android release and its APK."""

    def __init__(self, data_dir: Path) -> None:
        self.dir = Path(data_dir) / "update"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.release_path = self.dir / "release.json"
        self.apk_path = self.dir / "auri.apk"

    def get(self) -> AppRelease:
        if not self.release_path.exists():
            return AppRelease()
        try:
            data = json.loads(self.release_path.read_text("utf-8"))
            return AppRelease(**data)
        except (OSError, ValueError, TypeError):
            return AppRelease()

    def set_metadata(
        self,
        *,
        version_code: int,
        version_name: str,
        force: bool,
        changelog: str,
        download_url: str | None = None,
    ) -> AppRelease:
        release = self.get()
        release.version_code = version_code
        release.version_name = version_name
        release.force = force
        release.changelog = changelog
        if download_url is not None:
            release.download_url = download_url
        release.updated_at = time.time()
        self._write(release)
        return release

    def save_apk(self, data: bytes) -> AppRelease:
        digest = hashlib.sha256(data).hexdigest()
        versioned_path = self.dir / f"auri-{digest}.apk"
        if not versioned_path.exists():
            versioned_path.write_bytes(data)
        self.apk_path.write_bytes(data)
        release = self.get()
        release.sha256 = digest
        release.apk_size = len(data)
        release.download_url = f"/update/apk/{digest}"
        release.updated_at = time.time()
        self._write(release)
        return release

    def apk_path_for_sha256(self, sha256: str) -> Path | None:
        digest = sha256.lower()
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            return None
        versioned_path = self.dir / f"auri-{digest}.apk"
        if versioned_path.exists():
            return versioned_path
        if not self.apk_path.exists():
            return None
        if hashlib.sha256(self.apk_path.read_bytes()).hexdigest() != digest:
            return None
        versioned_path.write_bytes(self.apk_path.read_bytes())
        return versioned_path

    def has_apk(self) -> bool:
        return self.apk_path.exists()

    def apk_size_on_disk(self) -> int:
        return self.apk_path.stat().st_size if self.apk_path.exists() else 0

    def _write(self, release: AppRelease) -> None:
        payload = json.dumps(asdict(release), ensure_ascii=False, indent=2)
        self.release_path.write_text(payload, "utf-8")
