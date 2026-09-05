from __future__ import annotations

from pathlib import Path

from app.memory.intents import Intent, IntentKind, IntentStatus, IntentStore
from app.memory.told import Told, ToldFeedback, ToldStore


def test_intent_crud(tmp_dir: Path) -> None:
    store = IntentStore(tmp_dir / "memory.db")
    intent = Intent(
        user_id="u1",
        kind=IntentKind.event,
        trigger={"keywords": ["release"]},
        status=IntentStatus.armed,
    )
    store.save(intent)

    assert store.get(intent.id, "u1") is not None
    assert store.list("u1", status=IntentStatus.armed)[0].trigger == {"keywords": ["release"]}
    assert store.delete(intent.id, "u1") is True
    assert store.get(intent.id, "u1") is None


def test_told_dedup(tmp_dir: Path) -> None:
    store = ToldStore(tmp_dir / "memory.db")
    store.save(Told(user_id="u1", insight_key="insight-1"))

    assert store.has_told("insight-1", "u1") is True
    assert store.has_told("insight-2", "u1") is False
    assert store.list("u1")[0].user_feedback is ToldFeedback.none


def test_told_feedback_update(tmp_dir: Path) -> None:
    store = ToldStore(tmp_dir / "memory.db")
    store.save(Told(user_id="u1", insight_key="insight-1"))

    assert store.update_feedback(
        "insight-1", "u1", "default", ToldFeedback.unhelpful
    ) is True
    updated = store.get("insight-1", "u1")
    assert updated is not None
    assert updated.user_feedback is ToldFeedback.unhelpful
