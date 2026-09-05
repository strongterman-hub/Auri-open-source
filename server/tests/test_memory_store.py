from pathlib import Path

from app.memory.file_store import FileMemoryStore
from app.memory.models import (
    MemoryAction,
    MemoryOperation,
    MemoryScope,
    MemoryTarget,
    OriginClass,
)


def test_add_loads_durable_entries(tmp_dir: Path) -> None:
    store = FileMemoryStore(tmp_dir)
    scope = MemoryScope(user_id="u1", agent_id="a1")

    result = store.write(
        scope,
        [
            MemoryOperation(
                action=MemoryAction.add,
                target=MemoryTarget.memory,
                content="Project uses FastAPI.",
            ),
            MemoryOperation(
                action=MemoryAction.add,
                target=MemoryTarget.user,
                content="User prefers concise answers.",
            ),
        ],
    )

    assert result.success is True
    snapshot = store.load(scope)
    assert [entry.content for entry in snapshot.memory] == ["Project uses FastAPI."]
    assert [entry.content for entry in snapshot.user] == ["User prefers concise answers."]


def test_replace_and_remove_use_substring_matching(tmp_dir: Path) -> None:
    store = FileMemoryStore(tmp_dir)
    scope = MemoryScope(user_id="u1")

    store.write(
        scope,
        [MemoryOperation(action=MemoryAction.add, target=MemoryTarget.memory, content="old fact")],
    )
    store.write(
        scope,
        [
            MemoryOperation(
                action=MemoryAction.replace,
                target=MemoryTarget.memory,
                old_text="old",
                content="new fact",
            )
        ],
    )

    snapshot = store.load(scope)
    assert [entry.content for entry in snapshot.memory] == ["new fact"]

    store.write(
        scope,
        [MemoryOperation(action=MemoryAction.remove, target=MemoryTarget.memory, old_text="new")],
    )
    assert store.load(scope).memory == []


def test_entries_carry_provenance_and_replace_inherits_lineage(tmp_dir: Path) -> None:
    store = FileMemoryStore(tmp_dir)
    scope = MemoryScope(user_id="u1")

    store.write(
        scope,
        [
            MemoryOperation(
                action=MemoryAction.add,
                target=MemoryTarget.memory,
                content="Prefers dark mode",
                origin=OriginClass.owner,
                importance=8,
                trigger=["theme", "appearance"],
            )
        ],
    )

    first = store.load(scope).memory[0]
    assert first.origin is OriginClass.owner
    assert first.importance == 8
    assert first.trigger == ["theme", "appearance"]

    store.write(
        scope,
        [
            MemoryOperation(
                action=MemoryAction.replace,
                target=MemoryTarget.memory,
                old_text="dark mode",
                content="Prefers light mode",
                origin=OriginClass.agent,
            )
        ],
    )

    replaced = store.load(scope).memory[0]
    assert replaced.content == "Prefers light mode"
    assert replaced.origin is OriginClass.agent
    assert replaced.supersession_key == first.id
