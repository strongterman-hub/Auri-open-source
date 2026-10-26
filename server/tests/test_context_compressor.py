from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.agent.context_compressor import ContextCompressor
from app.agent.llm import LLMClient, LLMResponse, StreamChunk
from app.agent.runner import (
    AgentRunContext,
    AgentRunner,
    AgentStreamEvent,
    AgentTurn,
    _llm_history,
    _system_content,
)
from app.config import Settings
from app.memory.models import MemoryScope, MemorySnapshot
from app.services.agent_service import AgentService
from app.services.session_service import SessionService
from app.session.models import Session, SessionCreate
from app.session.store import SessionStore


class FakeLLM(LLMClient):
    def __init__(self, summary: str = "compacted-summary") -> None:
        self.summary = summary
        self.calls: list[list[dict]] = []

    async def complete(self, messages, tools=None, max_tokens=None):
        self.calls.append(messages)
        return LLMResponse(content=self.summary, usage={"prompt_tokens": 0})

    async def stream(self, messages, tools=None, max_tokens=None):
        yield StreamChunk(content=self.summary)
        yield StreamChunk(done=True)


class FakeRunner(AgentRunner):
    def __init__(self) -> None:
        self.contexts: list[AgentRunContext] = []

    async def run(self, context: AgentRunContext) -> AgentTurn:
        self.contexts.append(context)
        return AgentTurn(text="ok", usage={"prompt_tokens": 1234})

    async def run_stream(self, context: AgentRunContext):
        self.contexts.append(context)
        yield AgentStreamEvent(content="ok")
        yield AgentStreamEvent(done=True, turn=AgentTurn(text="ok", usage={"prompt_tokens": 1234}))


class InMemorySessionStore(SessionStore):
    def __init__(self) -> None:
        self.sessions: dict[str, Session] = {}

    async def create(self, payload: SessionCreate) -> Session:
        session = Session.create(payload, uuid4().hex)
        self.sessions[session.id] = session
        return session

    async def get(self, session_id: str) -> Session | None:
        return self.sessions.get(session_id)

    async def save(self, session: Session) -> Session:
        self.sessions[session.id] = session
        return session

    async def delete(self, session_id: str) -> bool:
        return self.sessions.pop(session_id, None) is not None

    async def list(self) -> list[Session]:
        return list(self.sessions.values())


class FakeMemoryService:
    async def snapshot(self, scope: MemoryScope) -> MemorySnapshot:
        return MemorySnapshot()

    def build_system_prompt(self, snapshot: MemorySnapshot) -> str:
        return "No durable memory is available yet."


def _settings(**overrides) -> Settings:
    base = dict(
        context_length_override=200,
        context_chars_per_token=4,
        compression_threshold_ratio=0.75,
        compression_tail_ratio=0.5,
    )
    base.update(overrides)
    return Settings(**base)


def _session(message_count: int = 20) -> Session:
    now = datetime.now(timezone.utc)
    messages = []
    for index in range(message_count):
        role = "user" if index % 2 == 0 else "assistant"
        messages.append({"role": role, "content": ("a" * 400) + str(index)})
    return Session(id="s1", user_id="u1", messages=messages, created_at=now, updated_at=now)


def _service(
    session: Session,
    *,
    compressor: ContextCompressor | None = None,
    settings: Settings | None = None,
) -> tuple[AgentService, InMemorySessionStore, FakeRunner]:
    store = InMemorySessionStore()
    store.sessions[session.id] = session
    runner = FakeRunner()
    service = AgentService(
        session_service=SessionService(store),
        memory_service=FakeMemoryService(),
        runner=runner,
        tool_factory=lambda scope: [],
        compressor=compressor,
        settings=settings,
    )
    return service, store, runner


def test_estimate_tokens_and_thresholds() -> None:
    compressor = ContextCompressor(FakeLLM(), _settings())
    assert compressor.estimate_tokens("a" * 400) == 100
    assert compressor.threshold_tokens == 150
    assert compressor.tail_token_budget == 75


def test_unicode_and_provider_usage_trigger_compaction() -> None:
    compressor = ContextCompressor(FakeLLM(), Settings(_env_file=None))
    assert compressor.estimate_tokens("中文" * 100) >= 200
    session = _session(message_count=2)
    session.last_prompt_tokens = 118090
    assert compressor.needs_compaction(session, "")


def test_compacted_archive_does_not_count_again() -> None:
    compressor = ContextCompressor(FakeLLM(), Settings(_env_file=None))
    session = _session(message_count=1000)
    session.mark_compacted("Facts only.", 998, 1)
    assert compressor.estimate_prompt_tokens(session, "") < 5000
    assert not compressor.needs_compaction(session, "")


def test_recent_tail_is_bounded_and_transcript_stays_intact() -> None:
    compressor = ContextCompressor(FakeLLM(), Settings(_env_file=None))
    session = _session(message_count=100)
    original = [dict(m) for m in session.messages]
    result = asyncio_run(compressor.compact(session))
    assert result is not None and result.cursor == 76
    assert session.messages == original


def test_pending_user_burst_is_never_absorbed() -> None:
    compressor = ContextCompressor(FakeLLM(), Settings(_env_file=None))
    session = _session(message_count=40)
    session.messages.extend({"role": "user", "content": str(i)} for i in range(30))
    result = asyncio_run(compressor.compact(session))
    assert result is not None and result.cursor == 40


def test_summary_failure_preserves_unsummarized_prefix() -> None:
    class PartialLLM(FakeLLM):
        async def complete(self, messages, tools=None, max_tokens=None):
            if self.calls:
                return LLMResponse(content="incomplete", finish_reason="length")
            return await super().complete(messages, tools, max_tokens)
    llm = PartialLLM()
    compressor = ContextCompressor(llm, _settings(compression_chunk_tokens=250))
    session = _session()
    result = asyncio_run(compressor.compact(session))
    assert result is not None and result.cursor == 2
    assert len(session.messages) == 20


def test_single_large_message_is_absorbed_in_bounded_fragments() -> None:
    llm = FakeLLM()
    compressor = ContextCompressor(llm, _settings(compression_chunk_tokens=1200))
    session = _session(message_count=2)
    session.messages[0]["content"] = "汉" * 2000
    result = asyncio_run(compressor.compact(session))
    assert result is not None and result.cursor == 1
    assert len(llm.calls) >= 4
    assert all(len(call[1]["content"]) < 1000 for call in llm.calls)


def test_checkpoint_only_advances_successful_chunks() -> None:
    checkpoints = []
    compressor = ContextCompressor(FakeLLM(), _settings(compression_chunk_tokens=1200))
    result = asyncio_run(compressor.compact(_session(), on_checkpoint=checkpoints.append))
    assert result is not None and len(checkpoints) > 1
    assert checkpoints[-1] == result
    assert [c.cursor for c in checkpoints] == sorted({c.cursor for c in checkpoints})


def test_fact_handoff_excludes_old_assistant_behavior() -> None:
    from app.agent.context_compressor import SUMMARY_SYSTEM_PROMPT
    assert "never infer preferences from the assistant's old wording" in SUMMARY_SYSTEM_PROMPT
    assert "old assistant tone" in SUMMARY_SYSTEM_PROMPT


def test_compact_advances_cursor_and_preserves_tail() -> None:
    compressor = ContextCompressor(FakeLLM(), _settings())
    session = _session()
    previous_cursor = session.summary_cursor

    result = asyncio_run(compressor.compact(session))

    assert result is not None
    assert result.cursor > previous_cursor
    assert result.summary == "compacted-summary"
    assert len(session.messages[result.cursor :]) < len(session.messages)


def test_compact_iterative_summary_includes_previous_summary() -> None:
    llm = FakeLLM()
    compressor = ContextCompressor(llm, _settings())
    session = _session()
    session.summary = "previous-summary"
    session.summary_cursor = 0

    asyncio_run(compressor.compact(session))

    user_content = llm.calls[0][1]["content"]
    assert "previous-summary" in user_content
    assert "New turns to absorb" in user_content


def test_agent_service_compacts_without_rotating() -> None:
    llm = FakeLLM()
    compressor = ContextCompressor(llm, _settings())
    service, store, runner = _service(_session(), compressor=compressor)

    turn, _scope, final = asyncio_run(service.send("s1", "hello"))

    context = runner.contexts[-1]
    assert context.summary == "compacted-summary"
    assert len(context.history) < 20
    assert final.id == "s1"
    assert final.parent_session_id is None
    assert final.summary == "compacted-summary"
    assert final.summary_cursor > 0
    assert store.sessions["s1"].end_reason is None
    assert turn.text == "ok"


def test_agent_service_marks_current_message_for_model_routing() -> None:
    service, _store, runner = _service(_session(message_count=2))
    asyncio_run(service.send("s1", "hello"))
    context = runner.contexts[-1]
    assert context.current_message_ids == [context.history[-1]["id"]]


def test_agent_service_resets_on_idle() -> None:
    session = _session(message_count=20)
    session.updated_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    settings = _settings(session_reset_policy="idle", session_idle_minutes=1)
    service, store, _runner = _service(session, settings=settings)

    _turn, _scope, final = asyncio_run(service.send("s1", "hello"))

    assert final.id != "s1"
    assert final.parent_session_id == "s1"
    assert final.reset_notice is not None
    assert store.sessions["s1"].end_reason == "session_reset"
    assert len(final.messages) == 2  # new user + assistant, old history archived


def test_llm_history_strips_persistence_fields() -> None:
    cleaned = _llm_history(
        [
            {"id": "a", "role": "user", "content": "hi", "timestamp": "2026-01-01T00:00:00Z"},
            {"id": "b", "role": "assistant", "content": "hello", "timestamp": "2026-01-01T00:00:01Z"},
        ]
    )
    assert cleaned == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]


def test_system_content_injects_summary_as_background() -> None:
    context = AgentRunContext(
        session_id="s1",
        scope=MemoryScope(user_id="u1"),
        history=[],
        memory_prompt="memory",
        summary="the-summary",
    )
    content = _system_content(context)
    assert "the-summary" in content
    assert "BACKGROUND REFERENCE ONLY" in content


def test_system_content_maps_persisted_timestamps_to_turn_order() -> None:
    context = AgentRunContext(
        session_id="s1",
        scope=MemoryScope(user_id="u1"),
        history=[
            {
                "role": "user",
                "content": "我搬进新家了",
                "timestamp": "2026-09-02T03:29:00+00:00",
            }
        ],
        memory_prompt="memory",
    )

    content = _system_content(context)

    assert "MESSAGE TIMES — AUTHORITATIVE" in content
    assert "1. user @ 2026-09-02T03:29:00+00:00" in content


def test_compressor_input_preserves_turn_timestamps() -> None:
    llm = FakeLLM()
    compressor = ContextCompressor(llm, _settings())
    session = _session()
    session.messages[0]["timestamp"] = "2026-09-01T18:13:00+00:00"

    asyncio_run(compressor.compact(session))

    assert "[2026-09-01T18:13:00+00:00] user:" in llm.calls[0][1]["content"]


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)
