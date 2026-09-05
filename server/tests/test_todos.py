from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.agent.tools import TodoTool
from app.todos import Todo, TodoStatus, TodoStore


def test_todo_store_roundtrip(tmp_dir: Path) -> None:
    store = TodoStore(tmp_dir / "todos.db")
    todo = store.add(Todo(user_id="u1", title="买牛奶"))

    assert store.list("u1")[0].title == "买牛奶"
    assert store.complete(todo.id, "u1") is True
    assert store.list("u1")[0].status is TodoStatus.done
    assert store.complete(todo.id, "u1") is False


def test_todo_tool_add_list_complete(tmp_dir: Path) -> None:
    store = TodoStore(tmp_dir / "todos.db")
    tool = TodoTool(store, "u1", "default")

    added = json.loads(asyncio.run(tool.execute(action="add", title="喝水")))
    todo_id = added["todo"]["id"]

    listed = json.loads(asyncio.run(tool.execute(action="list")))
    assert listed["todos"][0]["title"] == "喝水"

    completed = json.loads(asyncio.run(tool.execute(action="complete", id=todo_id)))
    assert completed["completed"] is True
