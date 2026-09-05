from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).parents[1] / "deploy" / "migrate_user_email.py"
SPEC = importlib.util.spec_from_file_location("migrate_user_email", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
migration = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = migration
SPEC.loader.exec_module(migration)


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_migrates_all_supported_storage_shapes(tmp_path: Path) -> None:
    old_email = "test@example.org"
    new_email = "owner@example.net"
    data_dir = tmp_path / "data"
    auth_dir = data_dir / "auth"
    session_path = data_dir / "sessions" / "session-1.json"
    log_path = data_dir / "logs" / "token_usage.jsonl"
    billing_path = data_dir / "billing" / "billing.db"
    health_path = data_dir / "health" / "health.db"
    xiaomi_dir = data_dir / "xiaomi_credentials"

    write_json(
        auth_dir / "users.json",
        {
            old_email: {
                "id": old_email,
                "email": old_email,
                "password_hash": "unchanged",
                "salt": "unchanged",
            }
        },
    )
    write_json(
        auth_dir / "tokens.json",
        {"secret-token": {"user_id": old_email, "expires_at": "2099-01-01T00:00:00+00:00"}},
    )
    write_json(auth_dir / "user_timezones.json", {old_email: "Asia/Shanghai"})
    write_json(
        session_path,
        {"id": "session-1", "user_id": old_email, "agent_id": "default", "messages": []},
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        json.dumps({"user_id": old_email, "nested": {"user_id": old_email}}) + "\n",
        encoding="utf-8",
    )

    billing_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(billing_path) as connection:
        connection.execute("CREATE TABLE credit_accounts (user_id TEXT PRIMARY KEY, balance INTEGER)")
        connection.execute(
            "CREATE TABLE credit_ledger (user_id TEXT, reference TEXT UNIQUE)"
        )
        connection.execute("INSERT INTO credit_accounts VALUES (?, ?)", (old_email, 500))
        connection.execute(
            "INSERT INTO credit_ledger VALUES (?, ?)",
            (old_email, f"welcome:{old_email}"),
        )

    health_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(health_path) as connection:
        connection.execute(
            "CREATE TABLE health_metrics (user_id TEXT, metric_type TEXT, day TEXT, "
            "PRIMARY KEY (user_id, metric_type, day))"
        )
        connection.execute(
            "INSERT INTO health_metrics VALUES (?, 'steps', '2026-09-04')",
            (old_email,),
        )

    source_memory = migration.memory_dir(data_dir, old_email, "default")
    write_json(source_memory / "user.json", [{"content": "kept"}])
    source_xiaomi = migration.xiaomi_path(xiaomi_dir, old_email)
    write_json(source_xiaomi, {"encrypted": "kept"})

    plan = migration.build_plan(data_dir, xiaomi_dir, old_email, new_email)
    result = migration.apply_migration(data_dir, xiaomi_dir, plan)

    assert result["auth_user"] is True
    users = migration.read_json_object(auth_dir / "users.json")
    assert old_email not in users
    assert users[new_email]["id"] == new_email
    assert users[new_email]["email"] == new_email
    assert users[new_email]["password_hash"] == "unchanged"
    assert migration.read_json_object(auth_dir / "tokens.json")["secret-token"]["user_id"] == new_email
    assert migration.read_json_object(session_path)["user_id"] == new_email
    assert migration.memory_dir(data_dir, new_email, "default").exists()
    assert not source_memory.exists()
    assert migration.xiaomi_path(xiaomi_dir, new_email).exists()
    assert not source_xiaomi.exists()

    with sqlite3.connect(billing_path) as connection:
        assert connection.execute("SELECT user_id FROM credit_accounts").fetchone()[0] == new_email
        assert connection.execute("SELECT reference FROM credit_ledger").fetchone()[0] == f"welcome:{new_email}"
    with sqlite3.connect(health_path) as connection:
        assert connection.execute("SELECT user_id FROM health_metrics").fetchone()[0] == new_email

    migrated_log = json.loads(log_path.read_text(encoding="utf-8"))
    assert migrated_log["user_id"] == new_email
    assert migrated_log["nested"]["user_id"] == new_email

    reverse_plan = migration.build_plan(data_dir, xiaomi_dir, new_email, old_email)
    assert reverse_plan.source_sqlite_total == 3
    assert reverse_plan.source_tokens == 1
    assert len(reverse_plan.source_sessions) == 1
