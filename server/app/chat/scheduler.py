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
        self._delivery_task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="auri-chat-reply-scheduler")
            self._delivery_task = asyncio.create_task(self._deliver(), name="auri-chat-outbox")

    async def stop(self) -> None:
        if self._delivery_task is not None:
            self._delivery_task.cancel()
            try:
                await self._delivery_task
            except asyncio.CancelledError:
                pass
            self._delivery_task = None
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _deliver(self) -> None:
        while True:
            try:
                await self.service.deliver_one()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("outbox tick failed")
            await asyncio.sleep(min(self.tick_seconds, 0.25))

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
