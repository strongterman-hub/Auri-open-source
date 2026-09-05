from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4


@dataclass
class StoredFile:
    id: str
    name: str
    mime: str
    size: int
    text: str | None = None


class FileStore:
    """Persist non-image file attachments to disk and keep light metadata nearby."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def save(
        self,
        name: str,
        mime: str,
        data: bytes,
        text: str | None = None,
    ) -> StoredFile:
        file_id = uuid4().hex
        (self.root / file_id).write_bytes(data)
        meta = {
            "id": file_id,
            "name": name,
            "mime": mime,
            "size": len(data),
            "text": text,
        }
        (self.root / f"{file_id}.meta.json").write_text(
            json.dumps(meta, ensure_ascii=False),
            encoding="utf-8",
        )
        return StoredFile(**meta)

    def read(self, file_id: str) -> bytes | None:
        path = self.root / file_id
        if not path.exists():
            return None
        return path.read_bytes()

    def get(self, file_id: str) -> StoredFile | None:
        path = self.root / f"{file_id}.meta.json"
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        return StoredFile(**payload)

    def delete(self, file_id: str) -> bool:
        data_path = self.root / file_id
        meta_path = self.root / f"{file_id}.meta.json"
        removed = False
        if data_path.exists():
            data_path.unlink()
            removed = True
        if meta_path.exists():
            meta_path.unlink()
            removed = True
        return removed
