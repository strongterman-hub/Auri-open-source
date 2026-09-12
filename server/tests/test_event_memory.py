from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.agent.llm import LLMClient, LLMResponse
from app.memory.event_extraction import EventExtractor
from app.memory.events import (
    EventCandidate,
    EventDetailCandidate,
    EventMemoryStore,
    EventRelationType,
    EventStatus,
    TimePrecision,
)
from app.memory.models import MemoryScope, OriginClass
from app.observation.models import ObservationSource
from app.observation.store import ObservationStore
from app.services.event_memory_service import EventMemoryService


UTC = timezone.utc


class _CapturingExtractionLLM(LLMClient):
    def __init__(self, content: str = "[]") -> None:
        self.content = content
        self.messages: list[dict] = []

    async def complete(self, messages, tools=None, max_tokens=None):
        self.messages = list(messages)
        return LLMResponse(content=self.content)

    async def stream(self, messages, tools=None, max_tokens=None):
        raise NotImplementedError


def _candidate(
    key: str,
    summary: str,
    occurred_at: datetime,
    *,
    status: EventStatus = EventStatus.completed,
    thread_key: str = "move_2026_09",
    details: list[EventDetailCandidate] | None = None,
) -> EventCandidate:
    return EventCandidate(
        event_key=key,
        thread_key=thread_key,
        kind="life_event",
        title=summary,
        core_summary=summary,
        status=status,
        occurred_start=occurred_at,
        time_precision=TimePrecision.minute,
        timezone="Asia/Shanghai",
        details=details or [],
    )


def test_event_upsert_changes_status_without_duplicating(tmp_path: Path) -> None:
    store = EventMemoryStore(tmp_path / "memory.db")
    planned_at = datetime(2026, 9, 1, 4, 0, tzinfo=UTC)
    first = store.upsert_candidate(
        user_id="u1",
        agent_id="default",
        candidate=_candidate(
            "move_new_home_2026_09",
            "计划搬进新住处",
            planned_at,
            status=EventStatus.planned,
        ),
        origin=OriginClass.owner,
        source_ref="session:s1:message:m1",
        now=planned_at,
    )
    completed = store.upsert_candidate(
        user_id="u1",
        agent_id="default",
        candidate=_candidate(
            "move_new_home_2026_09",
            "已经搬进新住处",
            datetime(2026, 9, 2, 3, 29, tzinfo=UTC),
        ).model_copy(update={"time_precision": TimePrecision.unknown}),
        origin=OriginClass.owner,
        source_ref="session:s1:message:m2",
        now=datetime(2026, 9, 2, 3, 29, tzinfo=UTC),
    )

    assert completed.id == first.id
    assert completed.status is EventStatus.completed
    assert completed.core_summary == "已经搬进新住处"
    assert completed.time_precision is TimePrecision.minute
    assert completed.source_refs == [
        "session:s1:message:m1",
        "session:s1:message:m2",
    ]
    assert len(store.query("u1")) == 1


def test_event_timeline_links_consecutive_events(tmp_path: Path) -> None:
    store = EventMemoryStore(tmp_path / "memory.db")
    sleep = store.upsert_candidate(
        user_id="u1",
        agent_id="default",
        candidate=_candidate(
            "sleep_before_move",
            "搬家前一晚在旧住处睡觉",
            datetime(2026, 9, 1, 18, 13, tzinfo=UTC),
        ),
        origin=OriginClass.owner,
        source_ref="session:s1:message:sleep",
    )
    move = store.upsert_candidate(
        user_id="u1",
        agent_id="default",
        candidate=_candidate(
            "move_new_home_2026_09",
            "搬进新住处",
            datetime(2026, 9, 2, 3, 29, tzinfo=UTC),
        ),
        origin=OriginClass.owner,
        source_ref="session:s1:message:move",
    )

    store.refresh_temporal_relations("u1", "default", "move_2026_09")

    sleep = store.get(sleep.id)
    move = store.get(move.id)
    assert sleep is not None and move is not None
    assert any(
        relation.target_event_id == move.id
        and relation.relation is EventRelationType.before
        for relation in sleep.relations
    )
    assert any(
        relation.target_event_id == sleep.id
        and relation.relation is EventRelationType.after
        for relation in move.relations
    )


def test_user_correction_supersedes_conflicting_event(tmp_path: Path) -> None:
    store = EventMemoryStore(tmp_path / "memory.db")
    wrong = store.upsert_candidate(
        user_id="u1",
        agent_id="default",
        candidate=_candidate(
            "sleep_in_new_home_2026_09_02",
            "那晚睡在新住处",
            datetime(2026, 9, 1, 18, 13, tzinfo=UTC),
        ),
        origin=OriginClass.agent,
        source_ref="session:s1:message:assistant_guess",
    )
    correction = _candidate(
        "sleep_before_move_2026_09_02",
        "那晚还没有搬家，不是在新住处睡的",
        datetime(2026, 9, 1, 18, 13, tzinfo=UTC),
        details=[
            EventDetailCandidate(
                detail_key="user_correction",
                content="用户明确纠正这不是新家第一晚",
                protected=True,
            )
        ],
    ).model_copy(update={"corrects_event_key": wrong.event_key})

    corrected = store.upsert_candidate(
        user_id="u1",
        agent_id="default",
        candidate=correction,
        origin=OriginClass.owner,
        source_ref="session:s1:message:user_correction",
    )

    superseded = store.get(wrong.id)
    assert superseded is not None
    assert superseded.superseded_by == corrected.id
    assert [event.id for event in store.query("u1")] == [corrected.id]
    assert any(
        relation.target_event_id == wrong.id
        and relation.relation is EventRelationType.corrects
        for relation in corrected.relations
    )
    assert corrected.details[0].protected is True


def test_decay_forgets_detail_but_keeps_event_core_and_protected_detail(
    tmp_path: Path,
) -> None:
    store = EventMemoryStore(tmp_path / "memory.db")
    observed_at = datetime(2026, 1, 1, tzinfo=UTC)
    event = store.upsert_candidate(
        user_id="u1",
        agent_id="default",
        candidate=_candidate(
            "move_new_home_2026_01",
            "用户在一月搬了家",
            observed_at,
            details=[
                EventDetailCandidate(
                    detail_key="curtain_color",
                    content="窗帘是浅灰色",
                    salience=0.1,
                ),
                EventDetailCandidate(
                    detail_key="correction",
                    content="这是搬家后的第一天，不是第一晚",
                    salience=0.1,
                    protected=True,
                ),
            ],
        ),
        origin=OriginClass.owner,
        source_ref="session:s1:message:m1",
        now=observed_at,
    )

    changed = store.apply_decay(
        "u1",
        now=observed_at + timedelta(days=120),
        threshold=0.28,
    )
    recalled = store.get(event.id)

    assert changed == 1
    assert recalled is not None
    assert recalled.core_summary == "用户在一月搬了家"
    details = {detail.detail_key: detail for detail in recalled.details}
    assert details["curtain_color"].forgotten_at is not None
    assert details["correction"].forgotten_at is None

    store.reinforce(event.id, now=observed_at + timedelta(days=121))
    reinforced = store.get(event.id)
    assert reinforced is not None
    assert all(detail.forgotten_at is None for detail in reinforced.details)
    assert store.query("u1", query="浅灰色")[0].id == event.id


def test_delete_user_cascades_event_details_and_relations(tmp_path: Path) -> None:
    store = EventMemoryStore(tmp_path / "memory.db")
    first = store.upsert_candidate(
        user_id="u1",
        agent_id="default",
        candidate=_candidate(
            "event_one",
            "第一件事",
            datetime(2026, 9, 1, tzinfo=UTC),
            details=[
                EventDetailCandidate(
                    detail_key="small_detail",
                    content="一个细节",
                )
            ],
        ),
        origin=OriginClass.owner,
        source_ref="session:s1:message:m1",
    )
    store.upsert_candidate(
        user_id="u1",
        agent_id="default",
        candidate=_candidate(
            "event_two",
            "第二件事",
            datetime(2026, 9, 2, tzinfo=UTC),
        ),
        origin=OriginClass.owner,
        source_ref="session:s1:message:m2",
    )
    store.refresh_temporal_relations("u1", "default", "move_2026_09")

    store.delete_user("u1")

    assert store.query("u1") == []
    assert store.get(first.id) is None
    with store._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM event_details").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM event_relations").fetchone()[0] == 0


class _FakeExtractor:
    async def extract(self, **_kwargs):
        return [
            _candidate(
                "move_new_home_2026_09",
                "用户于 2026-09-02 11:29 搬进新住处",
                datetime(2026, 9, 2, 3, 29, tzinfo=UTC),
            )
        ]


def test_capture_persists_owner_source_and_structured_event_idempotently(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "memory.db"
    observations = ObservationStore(db_path)
    service = EventMemoryService(
        EventMemoryStore(db_path),
        observations,
        extractor=_FakeExtractor(),
    )
    scope = MemoryScope(user_id="u1", agent_id="default")
    message = {
        "id": "m1",
        "role": "user",
        "content": "我刚刚搬进新家了",
        "timestamp": "2026-09-02T03:29:00+00:00",
    }

    asyncio.run(service.capture_user_message(scope, "s1", message))
    asyncio.run(service.capture_user_message(scope, "s1", message))

    raw = observations.query(
        "u1",
        source=ObservationSource.conversation,
        kind="user_message",
    )
    events = service.store.query("u1")
    assert len(raw) == 1
    assert raw[0].origin is OriginClass.owner
    assert raw[0].payload["text"] == "我刚刚搬进新家了"
    assert len(events) == 1
    assert events[0].source_refs == ["session:s1:message:m1"]


def test_event_extractor_receives_reply_context_for_short_answer() -> None:
    llm = _CapturingExtractionLLM()
    extractor = EventExtractor(llm)

    result = asyncio.run(
        extractor.extract(
            user_id="u1",
            message_text="已经弄好了",
            message_at=datetime(2026, 9, 12, 11, 23, tzinfo=UTC),
            timezone_name="Asia/Shanghai",
            recent_events=[],
            conversation_context=[
                {
                    "id": "question-1",
                    "role": "assistant",
                    "content": "新的小米手环今晚该到了吧？",
                    "timestamp": "2026-09-12T11:05:53+00:00",
                }
            ],
        )
    )

    assert result == []
    prompt = llm.messages[-1]["content"]
    assert "CONVERSATION_CONTEXT" in prompt
    assert "question-1" in prompt
    assert "新的小米手环今晚该到了吧" in prompt
    assert "USER_MESSAGE:\n已经弄好了" in prompt


def test_event_extraction_diagnostics_distinguish_invalid_results() -> None:
    assert EventExtractor.parse_with_diagnostics("").status == "empty"
    assert EventExtractor.parse_with_diagnostics("not-json").status == "invalid_json"
    assert EventExtractor.parse_with_diagnostics('{"event": 1}').status == "invalid_shape"
    assert EventExtractor.parse_with_diagnostics("[]").status == "no_candidates"


def test_event_memory_audit_records_ids_without_message_bodies(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    audit_path = tmp_path / "event_memory.jsonl"
    service = EventMemoryService(
        EventMemoryStore(db_path),
        ObservationStore(db_path),
        extractor=_FakeExtractor(),
        audit_path=audit_path,
    )
    message = {
        "id": "answer-1",
        "role": "user",
        "content": "已经弄好了",
        "timestamp": "2026-09-12T11:23:49+00:00",
    }
    context = [
        {
            "id": "question-1",
            "role": "assistant",
            "content": "新的小米手环今晚该到了吧？",
        }
    ]

    asyncio.run(
        service.capture_user_message(
            MemoryScope(user_id="u1"),
            "s1",
            message,
            conversation_context=context,
        )
    )

    raw = audit_path.read_text(encoding="utf-8")
    record = json.loads(raw)
    assert record["message_id"] == "answer-1"
    assert record["context_message_ids"] == ["question-1"]
    assert record["persisted_count"] == 1
    assert "已经弄好了" not in raw
    assert "新的小米手环" not in raw


def test_regression_sleep_before_move_is_not_called_first_night_in_new_home(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "memory.db"
    store = EventMemoryStore(db_path)
    service = EventMemoryService(store, ObservationStore(db_path))
    scope = MemoryScope(user_id="u1", agent_id="default")
    store.upsert_candidate(
        user_id="u1",
        agent_id="default",
        candidate=_candidate(
            "sleep_2026_09_01",
            "用户于 2026-09-02 02:13 至 10:09 睡了一觉",
            datetime(2026, 9, 1, 18, 13, tzinfo=UTC),
        ),
        origin=OriginClass.owner,
        source_ref="health:sleep:2026-09-01",
    )
    store.upsert_candidate(
        user_id="u1",
        agent_id="default",
        candidate=_candidate(
            "move_new_home_2026_09",
            "用户于 2026-09-02 11:29 搬进新住处",
            datetime(2026, 9, 2, 3, 29, tzinfo=UTC),
        ),
        origin=OriginClass.owner,
        source_ref="session:s1:message:move",
    )
    store.refresh_temporal_relations("u1", "default", "move_2026_09")

    context = asyncio.run(
        service.build_context(scope, now=datetime(2026, 9, 2, 8, tzinfo=UTC))
    )

    assert context.index("sleep_2026_09_01") < context.index("move_new_home_2026_09")
    assert "2026-09-01T18:13:00+00:00" in context
    assert "2026-09-02T03:29:00+00:00" in context
    assert "relations=before:move_new_home_2026_09" in context
    assert "Never infer that two facts happened at the same place" in context


def test_expired_in_progress_state_is_explicitly_not_current(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    store = EventMemoryStore(db_path)
    service = EventMemoryService(store, ObservationStore(db_path))
    candidate = _candidate(
        "waiting_for_delivery_2026_09_02",
        "用户当时在家等快递",
        datetime(2026, 9, 2, 2, tzinfo=UTC),
        status=EventStatus.in_progress,
    ).model_copy(
        update={"current_until": datetime(2026, 9, 2, 4, tzinfo=UTC)}
    )
    store.upsert_candidate(
        user_id="u1",
        agent_id="default",
        candidate=candidate,
        origin=OriginClass.owner,
        source_ref="session:s1:message:waiting",
    )

    context = asyncio.run(
        service.build_context(
            MemoryScope(user_id="u1"),
            now=datetime(2026, 9, 2, 8, tzinfo=UTC),
        )
    )

    assert "key=waiting_for_delivery_2026_09_02 status=in_progress" in context
    assert "active_now=false" in context
    assert "current_until=2026-09-02T04:00:00+00:00" in context
