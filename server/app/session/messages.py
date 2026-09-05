from __future__ import annotations

from typing import Any


def paginate_messages(
    messages: list[dict[str, Any]],
    before: str | None,
    limit: int,
) -> tuple[list[dict[str, Any]], bool]:
    """Return a page of messages and whether older messages remain.

    ``before`` is the id of the oldest message the client already has; the page
    returned is the ``limit`` messages strictly older than it, in ascending
    order. When ``before`` is ``None`` the latest ``limit`` messages are
    returned.
    """
    if before is None:
        page = messages[-limit:] if limit > 0 else []
        return page, len(messages) > limit

    index = next(
        (i for i, message in enumerate(messages) if message.get("id") == before),
        None,
    )
    if index is None:
        return [], False

    start = max(0, index - limit)
    return messages[start:index], start > 0
