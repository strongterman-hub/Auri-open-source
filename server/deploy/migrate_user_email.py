#!/usr/bin/env python3
"""Migrate an Auri account from one email-backed user_id to another.

The service must be stopped for ``--apply``. Run without ``--apply`` first to
perform a read-only preflight. The script intentionally does not merge two
accounts: any target-owned data aborts the migration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class MigrationError(RuntimeError):
    pass


@dataclass
class MigrationPlan:
    old_email: str
    new_email: str
    sqlite_source_rows: dict[str, int] = field(default_factory=dict)
    sqlite_target_rows: dict[str, int] = field(default_factory=dict)
    source_sessions: list[Path] = field(default_factory=list)
    target_sessions: list[Path] = field(default_factory=list)
    source_tokens: int = 0
    target_tokens: int = 0
    source_timezones: int = 0
    target_timezones: int = 0
    source_log_records: dict[str, int] = field(default_factory=dict)
    agent_ids: set[str] = field(default_factory=lambda: {"default"})
    source_memory_dirs: list[tuple[Path, Path]] = field(default_factory=list)
    target_memory_dirs: list[Path] = field(default_factory=list)
    source_xiaomi_path: Path | None = None
    target_xiaomi_path: Path | None = None
    verification_rows: int = 0

    @property
    def source_sqlite_total(self) -> int:
        return sum(self.sqlite_source_rows.values())

    @property
    def target_sqlite_total(self) -> int:
        return sum(self.sqlite_target_rows.values())


def normalize_email(value: str) -> str:
    email = value.strip().lower()
    if len(email) > 254 or email.count("@") != 1:
        raise MigrationError(f"Invalid email: {value!r}")
    local, domain = email.split("@", 1)
    if not local or not domain or "." not in domain:
        raise MigrationError(f"Invalid email: {value!r}")
    return email


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def read_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MigrationError(f"Cannot read valid JSON object from {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise MigrationError(f"Expected a JSON object in {path}")
    return payload


def preserve_file_metadata(source: Path, target: Path) -> None:
    stat = source.stat()
    os.chmod(target, stat.st_mode)
    if hasattr(os, "chown"):
        os.chown(target, stat.st_uid, stat.st_gid)


def write_json_object(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existed = path.exists()
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    tmp_path = Path(handle.name)
    try:
        with handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if existed:
            preserve_file_metadata(path, tmp_path)
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def sqlite_user_columns(connection: sqlite3.Connection) -> list[tuple[str, str]]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    result: list[tuple[str, str]] = []
    for (table,) in rows:
        columns = connection.execute(
            f"PRAGMA table_info({quote_identifier(str(table))})"
        ).fetchall()
        for column in columns:
            if str(column[1]).lower() == "user_id":
                result.append((str(table), str(column[1])))
    return result


def connect_read_only(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)


def count_jsonl_user_ids(path: Path, email: str) -> int:
    count = 0
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise MigrationError(
                        f"Invalid JSONL at {path}:{line_number}: {exc}"
                    ) from exc
                count += count_user_id_fields(payload, email)
    except OSError as exc:
        raise MigrationError(f"Cannot read {path}: {exc}") from exc
    return count


def count_user_id_fields(value: Any, email: str) -> int:
    if isinstance(value, dict):
        count = int(value.get("user_id") == email)
        return count + sum(count_user_id_fields(item, email) for item in value.values())
    if isinstance(value, list):
        return sum(count_user_id_fields(item, email) for item in value)
    return 0


def replace_user_id_fields(value: Any, old_email: str, new_email: str) -> int:
    changed = 0
    if isinstance(value, dict):
        if value.get("user_id") == old_email:
            value["user_id"] = new_email
            changed += 1
        for item in value.values():
            changed += replace_user_id_fields(item, old_email, new_email)
    elif isinstance(value, list):
        for item in value:
            changed += replace_user_id_fields(item, old_email, new_email)
    return changed


def rewrite_jsonl(path: Path, old_email: str, new_email: str) -> int:
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    tmp_path = Path(handle.name)
    changed = 0
    try:
        with path.open("r", encoding="utf-8") as source, handle:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    handle.write(line)
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise MigrationError(
                        f"Invalid JSONL at {path}:{line_number}: {exc}"
                    ) from exc
                line_changes = replace_user_id_fields(payload, old_email, new_email)
                changed += line_changes
                if line_changes:
                    handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
                    handle.write("\n")
                else:
                    handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        preserve_file_metadata(path, tmp_path)
        if changed:
            os.replace(tmp_path, path)
        else:
            tmp_path.unlink()
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
    return changed


def memory_dir(data_dir: Path, email: str, agent_id: str) -> Path:
    digest = hashlib.sha1(f"{agent_id}:{email}".encode("utf-8")).hexdigest()
    return data_dir / "memory" / digest


def xiaomi_path(xiaomi_dir: Path, email: str) -> Path:
    digest = hashlib.sha256(email.encode("utf-8")).hexdigest()
    return xiaomi_dir / f"{digest}.json"


def build_plan(data_dir: Path, xiaomi_dir: Path, old_email: str, new_email: str) -> MigrationPlan:
    plan = MigrationPlan(old_email=old_email, new_email=new_email)

    auth_dir = data_dir / "auth"
    users = read_json_object(auth_dir / "users.json")
    if old_email not in users:
        raise MigrationError(f"Source account does not exist: {old_email}")
    if new_email in users:
        raise MigrationError(f"Target account already exists: {new_email}")

    tokens = read_json_object(auth_dir / "tokens.json")
    plan.source_tokens = sum(
        1 for record in tokens.values() if isinstance(record, dict) and record.get("user_id") == old_email
    )
    plan.target_tokens = sum(
        1 for record in tokens.values() if isinstance(record, dict) and record.get("user_id") == new_email
    )

    timezones = read_json_object(auth_dir / "user_timezones.json")
    plan.source_timezones = int(old_email in timezones)
    plan.target_timezones = int(new_email in timezones)

    sessions_dir = data_dir / "sessions"
    for path in sorted(sessions_dir.glob("*.json")):
        payload = read_json_object(path)
        user_id = payload.get("user_id")
        if user_id == old_email:
            plan.source_sessions.append(path)
            agent_id = payload.get("agent_id")
            if isinstance(agent_id, str) and agent_id:
                plan.agent_ids.add(agent_id)
        elif user_id == new_email:
            plan.target_sessions.append(path)

    for path in sorted(data_dir.rglob("*.db")):
        if path.stat().st_size == 0:
            continue
        with connect_read_only(path) as connection:
            for table, column in sqlite_user_columns(connection):
                table_q = quote_identifier(table)
                column_q = quote_identifier(column)
                source_count = int(
                    connection.execute(
                        f"SELECT COUNT(*) FROM {table_q} WHERE {column_q} = ?",
                        (old_email,),
                    ).fetchone()[0]
                )
                target_count = int(
                    connection.execute(
                        f"SELECT COUNT(*) FROM {table_q} WHERE {column_q} = ?",
                        (new_email,),
                    ).fetchone()[0]
                )
                key = f"{path.relative_to(data_dir)}:{table}.{column}"
                if source_count:
                    plan.sqlite_source_rows[key] = source_count
                    if any(str(row[1]).lower() == "agent_id" for row in connection.execute(
                        f"PRAGMA table_info({table_q})"
                    ).fetchall()):
                        rows = connection.execute(
                            f"SELECT DISTINCT agent_id FROM {table_q} WHERE {column_q} = ?",
                            (old_email,),
                        ).fetchall()
                        plan.agent_ids.update(
                            str(row[0]) for row in rows if row[0] is not None and str(row[0])
                        )
                if target_count:
                    plan.sqlite_target_rows[key] = target_count

            if path == auth_dir / "verification_codes.db":
                try:
                    plan.verification_rows = int(
                        connection.execute(
                            "SELECT COUNT(*) FROM email_verification_codes WHERE email IN (?, ?)",
                            (old_email, new_email),
                        ).fetchone()[0]
                    )
                except sqlite3.OperationalError:
                    pass

    for path in sorted(data_dir.rglob("*.jsonl")):
        source_count = count_jsonl_user_ids(path, old_email)
        target_count = count_jsonl_user_ids(path, new_email)
        if source_count:
            plan.source_log_records[str(path.relative_to(data_dir))] = source_count
        if target_count:
            plan.sqlite_target_rows[f"jsonl:{path.relative_to(data_dir)}"] = target_count

    for agent_id in sorted(plan.agent_ids):
        source = memory_dir(data_dir, old_email, agent_id)
        target = memory_dir(data_dir, new_email, agent_id)
        if source.exists():
            plan.source_memory_dirs.append((source, target))
        if target.exists():
            plan.target_memory_dirs.append(target)

    source_xiaomi = xiaomi_path(xiaomi_dir, old_email)
    target_xiaomi = xiaomi_path(xiaomi_dir, new_email)
    plan.source_xiaomi_path = source_xiaomi if source_xiaomi.exists() else None
    plan.target_xiaomi_path = target_xiaomi if target_xiaomi.exists() else None

    conflicts: list[str] = []
    if plan.target_tokens:
        conflicts.append(f"{plan.target_tokens} target token(s)")
    if plan.target_timezones:
        conflicts.append("target timezone")
    if plan.target_sessions:
        conflicts.append(f"{len(plan.target_sessions)} target session(s)")
    if plan.target_sqlite_total:
        conflicts.append(f"{plan.target_sqlite_total} target SQLite/JSONL row(s)")
    if plan.target_memory_dirs:
        conflicts.append(f"{len(plan.target_memory_dirs)} target memory dir(s)")
    if plan.target_xiaomi_path is not None:
        conflicts.append("target Xiaomi credential")
    if conflicts:
        raise MigrationError("Target-owned data exists; refusing to merge: " + ", ".join(conflicts))

    return plan


def print_plan(plan: MigrationPlan, data_dir: Path, xiaomi_dir: Path) -> None:
    summary = {
        "source_account": plan.old_email,
        "target_account": plan.new_email,
        "data_dir": str(data_dir),
        "xiaomi_dir": str(xiaomi_dir),
        "tokens": plan.source_tokens,
        "sessions": len(plan.source_sessions),
        "sqlite_rows": plan.source_sqlite_total,
        "sqlite_breakdown": plan.sqlite_source_rows,
        "jsonl_user_id_fields": sum(plan.source_log_records.values()),
        "jsonl_breakdown": plan.source_log_records,
        "memory_scopes": len(plan.source_memory_dirs),
        "xiaomi_credential": plan.source_xiaomi_path is not None,
        "timezone": bool(plan.source_timezones),
        "verification_challenges_to_clear": plan.verification_rows,
        "agent_ids": sorted(plan.agent_ids),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def migrate_sqlite(data_dir: Path, old_email: str, new_email: str) -> dict[str, int]:
    changed: dict[str, int] = {}
    for path in sorted(data_dir.rglob("*.db")):
        if path.stat().st_size == 0:
            continue
        with sqlite3.connect(path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                for table, column in sqlite_user_columns(connection):
                    cursor = connection.execute(
                        f"UPDATE {quote_identifier(table)} SET {quote_identifier(column)} = ? "
                        f"WHERE {quote_identifier(column)} = ?",
                        (new_email, old_email),
                    )
                    if cursor.rowcount:
                        changed[f"{path.relative_to(data_dir)}:{table}.{column}"] = cursor.rowcount

                if path == data_dir / "billing" / "billing.db":
                    cursor = connection.execute(
                        "UPDATE credit_ledger SET reference = ? WHERE reference = ?",
                        (f"welcome:{new_email}", f"welcome:{old_email}"),
                    )
                    if cursor.rowcount:
                        changed[f"{path.relative_to(data_dir)}:credit_ledger.reference"] = cursor.rowcount

                if path == data_dir / "auth" / "verification_codes.db":
                    cursor = connection.execute(
                        "DELETE FROM email_verification_codes WHERE email IN (?, ?)",
                        (old_email, new_email),
                    )
                    if cursor.rowcount:
                        changed[f"{path.relative_to(data_dir)}:verification_deleted"] = cursor.rowcount
                connection.commit()
            except Exception:
                connection.rollback()
                raise
    return changed


def apply_migration(data_dir: Path, xiaomi_dir: Path, plan: MigrationPlan) -> dict[str, Any]:
    old_email = plan.old_email
    new_email = plan.new_email
    result: dict[str, Any] = {}

    result["sqlite"] = migrate_sqlite(data_dir, old_email, new_email)

    session_changes = 0
    for path in plan.source_sessions:
        payload = read_json_object(path)
        if payload.get("user_id") != old_email:
            raise MigrationError(f"Session changed after preflight: {path}")
        payload["user_id"] = new_email
        write_json_object(path, payload)
        session_changes += 1
    result["sessions"] = session_changes

    log_changes: dict[str, int] = {}
    for path in sorted(data_dir.rglob("*.jsonl")):
        changed = rewrite_jsonl(path, old_email, new_email)
        if changed:
            log_changes[str(path.relative_to(data_dir))] = changed
    result["jsonl"] = log_changes

    moved_memory = 0
    for source, target in plan.source_memory_dirs:
        if target.exists():
            raise MigrationError(f"Target memory path appeared after preflight: {target}")
        source.rename(target)
        moved_memory += 1
    result["memory_scopes"] = moved_memory

    moved_xiaomi = False
    if plan.source_xiaomi_path is not None:
        target = xiaomi_path(xiaomi_dir, new_email)
        if target.exists():
            raise MigrationError(f"Target Xiaomi path appeared after preflight: {target}")
        plan.source_xiaomi_path.rename(target)
        moved_xiaomi = True
    result["xiaomi_credential"] = moved_xiaomi

    auth_dir = data_dir / "auth"
    timezones = read_json_object(auth_dir / "user_timezones.json")
    if old_email in timezones:
        timezones[new_email] = timezones.pop(old_email)
        write_json_object(auth_dir / "user_timezones.json", timezones)
    result["timezone"] = new_email in timezones

    tokens = read_json_object(auth_dir / "tokens.json")
    token_changes = 0
    for record in tokens.values():
        if isinstance(record, dict) and record.get("user_id") == old_email:
            record["user_id"] = new_email
            token_changes += 1
    write_json_object(auth_dir / "tokens.json", tokens)
    result["tokens"] = token_changes

    users = read_json_object(auth_dir / "users.json")
    if old_email not in users or new_email in users:
        raise MigrationError("Auth users changed after preflight")
    record = users.pop(old_email)
    if not isinstance(record, dict):
        raise MigrationError("Source auth record is not an object")
    record["id"] = new_email
    record["email"] = new_email
    users[new_email] = record
    write_json_object(auth_dir / "users.json", users)
    result["auth_user"] = True

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("old_email")
    parser.add_argument("new_email")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--xiaomi-dir", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    data_dir = args.data_dir.resolve()
    xiaomi_dir = (args.xiaomi_dir or data_dir / "xiaomi_credentials").resolve()
    old_email = normalize_email(args.old_email)
    new_email = normalize_email(args.new_email)
    if old_email == new_email:
        raise MigrationError("Source and target emails are identical")

    plan = build_plan(data_dir, xiaomi_dir, old_email, new_email)
    print_plan(plan, data_dir, xiaomi_dir)
    if not args.apply:
        print("DRY RUN ONLY: no data changed")
        return 0

    result = apply_migration(data_dir, xiaomi_dir, plan)
    print(json.dumps({"applied": True, "changes": result}, ensure_ascii=False, indent=2))

    post_plan = build_plan(data_dir, xiaomi_dir, new_email, old_email)
    if post_plan.old_email != new_email:
        raise MigrationError("Post-migration verification failed")
    print("POSTCHECK OK: target owns the migrated data and source account is absent")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MigrationError as exc:
        print(f"ERROR: {exc}")
        raise SystemExit(2)
