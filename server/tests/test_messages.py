from app.session.messages import paginate_messages


def _messages(count: int) -> list[dict]:
    return [{"id": f"m{i}", "role": "user" if i % 2 == 0 else "assistant", "content": str(i)} for i in range(count)]


def test_latest_page() -> None:
    messages = _messages(25)
    page, has_more = paginate_messages(messages, None, 10)
    assert [m["id"] for m in page] == [f"m{i}" for i in range(15, 25)]
    assert has_more is True


def test_previous_page_by_before() -> None:
    messages = _messages(25)
    page, has_more = paginate_messages(messages, "m15", 10)
    assert [m["id"] for m in page] == [f"m{i}" for i in range(5, 15)]
    assert has_more is True


def test_no_more_older_messages() -> None:
    messages = _messages(25)
    page, has_more = paginate_messages(messages, "m4", 10)
    assert [m["id"] for m in page] == ["m0", "m1", "m2", "m3"]
    assert has_more is False


def test_unknown_before_returns_empty() -> None:
    page, has_more = paginate_messages(_messages(10), "missing", 10)
    assert page == []
    assert has_more is False
