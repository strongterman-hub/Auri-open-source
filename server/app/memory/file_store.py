from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.memory.base import MemoryStore
from app.memory.models import (
    MemoryAction,
    MemoryEntry,
    MemoryOperation,
    MemoryScope,
    MemorySnapshot,
    MemoryTarget,
    MemoryWriteResult,
    OriginClass,
)


def _dedupe(entries: list[MemoryEntry]) -> list[MemoryEntry]:
    """Keep the first occurrence of each entry, keyed by normalized content."""
    seen: set[str] = set()
    result: list[MemoryEntry] = []
    for entry in entries:
        key = entry.content.strip()
        if key in seen:
            continue
        seen.add(key)
        result.append(entry)
    return result


class FileMemoryStore(MemoryStore):
    """JSON-file-backed memory store with provenance-bearing entries.

    Legacy files were plain ``list[str]``; on read those are upgraded to
    ``MemoryEntry`` with ``origin=agent`` and ``observed_at`` set from the file
    mtime. The upgraded object format is persisted on the next write.
    """

    def __init__(
        self,
        root: Path,
        memory_char_limit: int = 2200,
        user_char_limit: int = 1375,
    ) -> None:
        self.root = Path(root)
        self.memory_char_limit = memory_char_limit
        self.user_char_limit = user_char_limit
        self._locks_guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}

    def _scope_dir(self, scope: MemoryScope) -> Path:
        digest = hashlib.sha1(scope.scope_key.encode("utf-8")).hexdigest()
        return self.root / digest

    def _lock_for(self, scope: MemoryScope) -> threading.Lock:
        key = scope.scope_key
        with self._locks_guard:
            return self._locks.setdefault(key, threading.Lock())

    def _file_for(self, scope_dir: Path, target: MemoryTarget) -> Path:
        return scope_dir / f"{target.value}.json"

    def _read_entries(self, path: Path) -> list[MemoryEntry]:
        if not path.exists():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        if not isinstance(payload, list):
            return []

        entries: list[MemoryEntry] = []
        for item in payload:
            entry = self._coerce_entry(item, path)
            if entry is not None:
                entries.append(entry)
        return entries

    @staticmethod
    def _coerce_entry(item: object, path: Path) -> MemoryEntry | None:
        """Upgrade a legacy string item or a serialized entry dict to MemoryEntry."""
        if isinstance(item, str):
            if not item.strip():
                return None
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            return MemoryEntry(content=item, origin=OriginClass.agent, observed_at=mtime)
        if isinstance(item, dict):
            try:
                return MemoryEntry.model_validate(item)
            except Exception:
                return None
        return None

    @staticmethod
    def _usage(entries: list[MemoryEntry]) -> int:
        return sum(len(entry.content) for entry in entries)

    def _load_unlocked(self, scope: MemoryScope) -> MemorySnapshot:
        scope_dir = self._scope_dir(scope)
        memory = _dedupe(self._read_entries(self._file_for(scope_dir, MemoryTarget.memory)))
        user = _dedupe(self._read_entries(self._file_for(scope_dir, MemoryTarget.user)))
        return MemorySnapshot(
            memory=memory,
            user=user,
            memory_usage=self._usage(memory),
            user_usage=self._usage(user),
        )

    def load(self, scope: MemoryScope) -> MemorySnapshot:
        with self._lock_for(scope):
            return self._load_unlocked(scope)

    @staticmethod
    def _build_entry(operation: MemoryOperation, supersession_key: str | None = None) -> MemoryEntry:
        return MemoryEntry(
            content=operation.content or "",
            origin=operation.origin,
            supersession_key=supersession_key,
            importance=operation.importance,
            trigger=operation.trigger,
            source_ref=operation.source_ref,
        )

    @staticmethod
    def _apply_operation(
        entries: list[MemoryEntry],
        operation: MemoryOperation,
    ) -> str | None:
        if operation.action is MemoryAction.add:
            if any(entry.content.strip() == (operation.content or "").strip() for entry in entries):
                return "Entry already exists (no duplicate added)."
            entries.append(FileMemoryStore._build_entry(operation))
            return None

        if operation.action is MemoryAction.remove:
            index = next(
                (i for i, entry in enumerate(entries) if operation.old_text in entry.content),
                None,
            )
            if index is None:
                return f"old_text '{operation.old_text}' did not match any entry."
            entries.pop(index)
            return None

        # replace: inherit the previous entry's lineage key so a conceptual fact
        # keeps a stable supersession identity across subsequent replaces.
        index = next(
            (i for i, entry in enumerate(entries) if operation.old_text in entry.content),
            None,
        )
        if index is None:
            return f"old_text '{operation.old_text}' did not match any entry."
        previous = entries[index]
        supersession_key = previous.supersession_key or previous.id
        entries[index] = FileMemoryStore._build_entry(operation, supersession_key=supersession_key)
        return None

    def _write_json_atomic(self, path: Path, entries: list[MemoryEntry]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = [entry.model_dump(mode="json") for entry in entries]
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp_path, path)

    def write(
        self,
        scope: MemoryScope,
        operations: list[MemoryOperation],
    ) -> MemoryWriteResult:
        with self._lock_for(scope):
            snapshot = self._load_unlocked(scope)
            next_memory = list(snapshot.memory)
            next_user = list(snapshot.user)

            for operation in operations:
                target_entries = (
                    next_user if operation.target is MemoryTarget.user else next_memory
                )
                error = self._apply_operation(target_entries, operation)
                if error:
                    return MemoryWriteResult(
                        success=False,
                        error=error,
                        target=operation.target,
                        entries=list(target_entries),
                        memory_usage=self._usage(next_memory),
                        user_usage=self._usage(next_user),
                    )

            memory_usage = self._usage(next_memory)
            user_usage = self._usage(next_user)
            if memory_usage > self.memory_char_limit:
                return MemoryWriteResult(
                    success=False,
                    error=(
                        f"memory exceeds its {self.memory_char_limit}-character budget "
                        f"({memory_usage} used)."
                    ),
                    target=MemoryTarget.memory,
                    entries=next_memory,
                    memory_usage=memory_usage,
                    user_usage=user_usage,
                )
            if user_usage > self.user_char_limit:
                return MemoryWriteResult(
                    success=False,
                    error=(
                        f"user memory exceeds its {self.user_char_limit}-character budget "
                        f"({user_usage} used)."
                    ),
                    target=MemoryTarget.user,
                    entries=next_user,
                    memory_usage=memory_usage,
                    user_usage=user_usage,
                )

            scope_dir = self._scope_dir(scope)
            self._write_json_atomic(
                self._file_for(scope_dir, MemoryTarget.memory), next_memory
            )
            self._write_json_atomic(
                self._file_for(scope_dir, MemoryTarget.user), next_user
            )

            final_entries = (
                next_user
                if operations and operations[-1].target is MemoryTarget.user
                else next_memory
            )
            return MemoryWriteResult(
                success=True,
                target=operations[-1].target if operations else None,
                entries=final_entries,
                memory_usage=memory_usage,
                user_usage=user_usage,
            )

    def delete_scope(self, scope: MemoryScope) -> None:
        scope_dir = self._scope_dir(scope)
        if scope_dir.exists():
            shutil.rmtree(scope_dir)
