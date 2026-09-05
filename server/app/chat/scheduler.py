from __future__ import annotations

import asyncio
import logging

from app.chat.reply_service import ChatReplyService


logger = logging.getLogger("auri.chat_reply")


class ChatReplyScheduler:
    def __init__(self, service: ChatReplyService, tick_seconds: float = 0.5) -> None:
        self.service = service
        self.tick_seconds = max(0.1, tick_seconds)
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="auri-chat-reply-scheduler")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        while True:
            try:
                worked = await self.service.process_one()
                if worked:
                    continue
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("chat reply scheduler tick failed")
            await asyncio.sleep(self.tick_seconds)
