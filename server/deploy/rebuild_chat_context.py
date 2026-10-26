"""Prepare a fact-only handoff, then apply it without deleting chat or memory.

Preparation uses an administrative model client without a billing usage sink.
Apply only while the server's session writers are stopped. It accepts appended
messages but rejects any change to the summarized prefix or prior summary.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.context_compressor import CompactionResult, ContextCompressor
from app.agent.provider import build_llm_client
from app.config import Settings
from app.core.token_logger import TokenLogger
from app.session.models import Session


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def read_session(data_dir: Path, session_id: str) -> Session:
    if Path(session_id).name != session_id or not session_id:
        raise ValueError("invalid_session_id")
    return Session.model_validate_json((data_dir / "sessions" / f"{session_id}.json").read_text(encoding="utf-8"))


async def prepare(data_dir: Path, session_id: str, candidate_path: Path, tail_messages: int) -> dict:
    session = read_session(data_dir, session_id)
    original_summary_hash = fingerprint([session.summary, session.summary_cursor, session.summary_generation])
    original_generation = session.summary_generation
    checkpoint_path = candidate_path.with_suffix(".checkpoint.json")
    if checkpoint_path.exists():
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if (checkpoint["previous_summary_hash"] != original_summary_hash
                or checkpoint["prefix_hash"] != fingerprint(session.messages[:checkpoint["cursor"]])):
            raise ValueError("stale_checkpoint")
        session.summary = checkpoint["summary"]
        session.summary_cursor = checkpoint["cursor"]
    settings = Settings()
    # Administrative maintenance is never charged to the user's Credits.
    llm = build_llm_client(settings, token_logger=TokenLogger(data_dir / "logs" / "context_maintenance.jsonl"))
    def save_checkpoint(result):
        checkpoint = {"previous_summary_hash": original_summary_hash,
                      "prefix_hash": fingerprint(session.messages[:result.cursor]),
                      "summary": result.summary, "cursor": result.cursor}
        temporary = checkpoint_path.with_suffix(".tmp")
        temporary.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(checkpoint, ensure_ascii=False), encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, checkpoint_path)
    result = await ContextCompressor(llm, settings).compact(
        session, max_tail_messages=tail_messages, on_checkpoint=save_checkpoint)
    if result is None and checkpoint_path.exists() and len(session.tail_messages()) <= tail_messages:
        result = CompactionResult(session.summary, session.summary_cursor, original_generation + 1)
    if result is None:
        raise ValueError("no_complete_handoff")
    if len(session.messages) - result.cursor > tail_messages:
        raise ValueError("handoff_incomplete_or_pending_user_burst")
    candidate = {
        "session_id": session.id, "user_id": session.user_id,
        "previous_summary_hash": original_summary_hash,
        "prefix_hash": fingerprint(session.messages[:result.cursor]),
        "source_message_count": len(session.messages),
        "summary": result.summary, "cursor": result.cursor, "generation": original_generation + 1,
        "prepared_at": datetime.now(timezone.utc).isoformat(),
    }
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    with candidate_path.open("x", encoding="utf-8") as handle:
        json.dump(candidate, handle, ensure_ascii=False, indent=2)
    os.chmod(candidate_path, 0o600)
    return {"status": "prepared", "source_messages": len(session.messages),
            "summarized_messages": result.cursor, "summary_chars": len(result.summary)}


def apply(data_dir: Path, candidate_path: Path, backup_dir: Path) -> dict:
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    session = read_session(data_dir, candidate["session_id"])
    cursor = candidate["cursor"]
    if session.user_id != candidate["user_id"] or not 0 < cursor <= len(session.messages):
        raise ValueError("invalid_handoff_target")
    if fingerprint([session.summary, session.summary_cursor, session.summary_generation]) != candidate["previous_summary_hash"]:
        raise ValueError("summary_changed_since_preparation")
    if fingerprint(session.messages[:cursor]) != candidate["prefix_hash"]:
        raise ValueError("history_prefix_changed_since_preparation")
    if not isinstance(candidate["summary"], str) or not candidate["summary"].strip():
        raise ValueError("empty_handoff")
    if candidate["generation"] != session.summary_generation + 1:
        raise ValueError("invalid_handoff_generation")
    original_messages_hash = fingerprint(session.messages)
    path = data_dir / "sessions" / f"{session.id}.json"
    original_stat = path.stat()
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / path.name
    with backup.open("xb") as handle:
        handle.write(path.read_bytes())
    os.chmod(backup, 0o600)
    session.mark_compacted(candidate["summary"], cursor, candidate["generation"])
    session.last_prompt_tokens = 0
    session.metadata["context_handoff"] = {
        "version": "facts-v2", "applied_at": datetime.now(timezone.utc).isoformat(),
        "archived_messages": cursor,
    }
    temporary = path.with_suffix(".handoff.tmp")
    temporary.write_text(session.model_dump_json(indent=2), encoding="utf-8")
    # Maintenance may run as root while the application runs as another user.
    # Atomic replacement must preserve the original file's access identity.
    if hasattr(os, "chown"):
        temporary_stat = temporary.stat()
        if (temporary_stat.st_uid, temporary_stat.st_gid) != (original_stat.st_uid, original_stat.st_gid):
            os.chown(temporary, original_stat.st_uid, original_stat.st_gid)
    os.chmod(temporary, stat.S_IMODE(original_stat.st_mode))
    os.replace(temporary, path)
    saved = read_session(data_dir, session.id)
    assert fingerprint(saved.messages) == original_messages_hash
    return {"status": "applied", "messages": len(saved.messages), "summary_cursor": saved.summary_cursor,
            "recent_messages": len(saved.tail_messages()), "summary_chars": len(saved.summary or ""),
            "transcript_unchanged": True}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--session-id")
    parser.add_argument("--tail-messages", type=int, default=4)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup-dir", type=Path)
    args = parser.parse_args()
    if args.apply:
        if args.backup_dir is None:
            parser.error("--apply requires --backup-dir and stopped session writers")
        result = apply(args.data_dir, args.candidate, args.backup_dir)
    else:
        if not args.session_id or args.tail_messages < 1:
            parser.error("preparation requires --session-id and positive --tail-messages")
        result = asyncio.run(prepare(args.data_dir, args.session_id, args.candidate, args.tail_messages))
    print(json.dumps(result))


if __name__ == "__main__":
    main()
