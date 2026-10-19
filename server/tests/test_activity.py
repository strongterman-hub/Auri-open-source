from __future__ import annotations

from datetime import datetime, timezone

from app.agent.activity import infer_current_activity


def test_activity_from_semantic_place() -> None:
    result = infer_current_activity(
        place={
            "place_name": "图书馆",
            "place_type": "图书馆",
            "place_label": "图书馆（图书馆）",
        },
        movement="stationary",
        local_now=datetime(2026, 9, 29, 14, 20, tzinfo=timezone.utc),
    )

    assert result.state == "studying"
    assert "图书馆" in (result.label or "")
    assert result.confidence > 0.5


def test_activity_prefers_explicit_user_words() -> None:
    result = infer_current_activity(
        place={"place_name": "教学楼", "place_type": "教学楼"},
        movement="stationary",
        recent_user_text="我正在开会，晚点说",
    )

    assert result.state == "meeting"
    assert result.user_stated is True
    assert "开会" in (result.label or "")
    assert "USER-STATED" in result.format()


def test_home_statement_is_authoritative_for_the_turn() -> None:
    result = infer_current_activity(
        place={"place_name": "东三旗村", "place_type": "住处/居住区"},
        movement="stationary",
        recent_user_text="我现在就在家呢，明天北京会下雨吗",
    )

    assert result.state == "at_home"
    assert result.user_stated is True
    text = result.format()
    assert "USER-STATED" in text
    assert "在家或住处" in text
    assert "NOT A FACT" not in text


def test_past_home_mention_is_not_treated_as_current_user_statement() -> None:
    result = infer_current_activity(recent_user_text="我昨天在家看了一天剧")

    assert result.state == "unknown"
    assert result.user_stated is False
    assert "NOT A FACT" in result.format()


def test_activity_from_active_schedule_event_is_a_plan_not_fact() -> None:
    result = infer_current_activity(
        active_event_title="组会",
        local_now=datetime(2026, 9, 29, 14, 20, tzinfo=timezone.utc),
    )

    assert result.state == "in_event"
    assert "组会" in (result.label or "")
    assert "计划" in "；".join(result.reasons)


def test_activity_hypothesis_format_marks_uncertainty() -> None:
    result = infer_current_activity()
    text = result.format()

    assert "NOT A FACT" in text
    assert "Do not pretend to know" in text
