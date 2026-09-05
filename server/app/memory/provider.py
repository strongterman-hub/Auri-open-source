from __future__ import annotations

from collections.abc import Callable

from app.config import Settings
from app.memory.base import MemoryStore
from app.memory.file_store import FileMemoryStore


MemoryStoreFactory = Callable[[Settings], MemoryStore]


class MemoryStoreRegistry:
    """A small registry that lets future backends be added without touching routes."""

    def __init__(self) -> None:
        self._factories: dict[str, MemoryStoreFactory] = {}
        self.register("file", self._build_file_store)

    def register(self, name: str, factory: MemoryStoreFactory) -> None:
        self._factories[name] = factory

    def create(self, settings: Settings) -> MemoryStore:
        factory = self._factories.get(settings.memory_backend)
        if factory is None:
            raise ValueError(f"Unknown memory backend: {settings.memory_backend}")
        return factory(settings)

    @staticmethod
    def _build_file_store(settings: Settings) -> MemoryStore:
        return FileMemoryStore(
            root=settings.data_dir / "memory",
            memory_char_limit=settings.default_memory_char_limit,
            user_char_limit=settings.default_user_char_limit,
        )


default_memory_store_registry = MemoryStoreRegistry()
