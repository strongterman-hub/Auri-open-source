import asyncio
import json
from types import SimpleNamespace

import pytest

from app.agent.llm import LLMResponse
from app.agent.runner import AgentRunContext, BasicAgentRunner
from app.chat.expression import parse_expression
from app.chat.reply_store import ChatReplyStore
from app.memory.models import MemoryScope
from app.persona.emotion import EmotionStore


def make_job(store, session="s", message="u1"):
    store.enqueue(session_id=session, user_id="u", agent_id="default", message_id=message,
                  debounce_seconds=0, capabilities=["message_sequence_v1", "stickers_v1"])
    planning = store.claim_due()
    store.finish_planning(planning.id, outcome="reply")
    return store.claim_due()


def due(store):
    with store._connect() as db:
        db.execute("UPDATE chat_reply_parts SET due_at=0")


def test_plan_decodes_without_exposing_protocol_or_cutting_code():
    raw = json.dumps({"auri_reply": 1, "messages": ["你过了？", "我就知道你这次准备得很稳。"],
                      "emotion": {"name": "happy", "intensity": .6, "reason": "通过了期待的考试"}})
    assert parse_expression(raw).text == "你过了？\n\n我就知道你这次准备得很稳。"
    assert parse_expression("```python\nx = 1\n```").messages == ["```python\nx = 1\n```"]
    for invalid in ('{"auri_reply":1,"messages":["valid",null]}', '{"auri_reply":1,"messages":['):
        with pytest.raises(ValueError):
            parse_expression(invalid)


def test_emotion_is_bounded_idempotent_decays_and_isolates_users(tmp_dir):
    store = EmotionStore(tmp_dir / "emotion.db")
    mood = {"name": "annoyed", "intensity": .8, "reason": "反复用同一句话冒犯"}
    assert store.apply("u", "a", "event", mood)
    assert not store.apply("u", "a", "event", mood)
    state = store.current("u", "a")
    assert .34 < state["intensity"] <= .35
    assert store.current("other", "a")["name"] == "calm"
    assert store.current("u", "a", now=state["updated_at"] + 86400)["name"] == "calm"
    assert EmotionStore(store.path).current("u", "a")["name"] == "annoyed"
    store.delete_user("u", "a")
    assert store.current("u", "a")["name"] == "calm"


def test_preferences_merge_preserve_account_isolation(tmp_dir):
    store = EmotionStore(tmp_dir / "emotion.db")
    store.set_preferences("u", stickers=False)
    assert store.set_preferences("u", continuous_messages=False) == {"stickers": False, "continuous_messages": False}
    assert store.preferences("other") == {"stickers": True, "continuous_messages": True}


def test_sticker_catalog_never_crosses_presentation(tmp_dir):
    from app.chat.expression import StickerCatalog
    (tmp_dir / "female-atlas.png").write_bytes(b"png")
    (tmp_dir / "male-atlas.png").write_bytes(b"png")
    (tmp_dir / "manifest.json").write_text(json.dumps({"stickers": [
        {"id":"female-happy", "presentation":"female", "file":"female-atlas.png"},
        {"id":"male-happy", "presentation":"male", "file":"male-atlas.png"},
        {"id":"bad-female", "presentation":"female", "file":"male-atlas.png"}
    ]}))
    catalog = StickerCatalog(tmp_dir)
    assert [x["id"] for x in catalog.entries("female")] == ["female-happy"]
    assert catalog.resolve("male-happy", "female") is None
    assert catalog.resolve("female-happy", "male") is None


def test_outbox_persists_order_and_recovers_expired_lease(tmp_dir):
    store = ChatReplyStore(tmp_dir / "replies.db")
    job = make_job(store)
    assert store.outbox.prepare(job, ["one", "two", "three"], {}, gap=1)
    first = store.outbox.claim()
    assert first["content"] == "one"
    assert store.outbox.claim() is None
    with store._connect() as db:
        db.execute("UPDATE chat_reply_parts SET lease_until=0 WHERE id=?", (first["id"],))
    recovered = ChatReplyStore(store.path).outbox.claim()
    assert recovered["id"] == first["id"]
    assert store.outbox.delivered(recovered) == (False, True)
    assert store.outbox.claim() is None
    due(store)
    second = store.outbox.claim()
    assert second["content"] == "two"
    assert store.outbox.delivered(second) == (False, False)
    due(store)
    assert store.outbox.delivered(store.outbox.claim()) == (True, False)
    assert store.reply_state("s") == "idle"


def test_interruption_cancels_tail_and_fences_stale_generation(tmp_dir):
    store = ChatReplyStore(tmp_dir / "replies.db")
    job = make_job(store)
    store.outbox.prepare(job, ["first", "old tail"], {}, gap=0)
    store.outbox.delivered(store.outbox.claim())
    new = store.enqueue(session_id="s", user_id="u", agent_id="default", message_id="u2",
                        debounce_seconds=0, capabilities=["message_sequence_v1"])
    assert new.message_ids == ["u2"]
    assert store.outbox.claim() is None
    assert not store.outbox.prepare(job, ["stale answer"], {}, gap=0)
    assert store.retry_or_fail(job.id, "late", max_attempts=3, delay_seconds=0) == "cancelled"


def test_unanswered_inputs_are_transferred_when_user_interrupts(tmp_dir):
    store = ChatReplyStore(tmp_dir / "replies.db")
    job = make_job(store)
    new = store.enqueue(session_id="s", user_id="u", agent_id="default", message_id="u2",
                        debounce_seconds=0, capabilities=["message_sequence_v1"])
    assert new.message_ids == ["u1", "u2"]
    assert not store.outbox.prepare(job, ["old response"], {}, gap=0)


def test_grounding_checks_plain_text_and_discards_fragments_after_rewrite():
    class LLM:
        async def complete(self, *a, **kw):
            return LLMResponse(content='{"auri_reply":1,"messages":["错误数字", "第二条"],"emotion":{"name":"calm","reason":"回应本轮问题"}}')
    class Grounded(BasicAgentRunner):
        async def _ground_reply(self, context, draft, *args):
            assert draft == "错误数字\n\n第二条"
            return "核实后的完整回复"
    result = asyncio.run(Grounded(LLM()).run(AgentRunContext(session_id="s", scope=MemoryScope(user_id="u"),
        history=[], memory_prompt="", expression_prompt="plan")))
    assert result.text == "核实后的完整回复"
    assert result.expression.messages == [result.text]
