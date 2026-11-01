"""
Центральный диспетчер событий.
Принимает события от всех каналов, оценивает приоритет,
передаёт агенту на анализ и отправляет результат в Telegram.
"""
import asyncio
import logging
from typing import Callable, Awaitable

from .event_types import Event, EventPriority

logger = logging.getLogger(__name__)

EventHandler = Callable[[Event], Awaitable[None]]


class Reactor:
    def __init__(self):
        self._queue: asyncio.Queue[Event] = asyncio.Queue()
        self._handlers: list[EventHandler] = []
        self._running = False

    def add_handler(self, handler: EventHandler) -> None:
        self._handlers.append(handler)

    async def push(self, event: Event) -> None:
        await self._queue.put(event)
        logger.info("Event queued: %s", event)

    async def run(self) -> None:
        self._running = True
        logger.info("Reactor started, waiting for events...")
        while self._running:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                await self._dispatch(event)
                self._queue.task_done()
            except asyncio.TimeoutError:
                continue
            except Exception:
                logger.exception("Error dispatching event")

    async def _dispatch(self, event: Event) -> None:
        logger.debug("Dispatching %s to %d handlers", event, len(self._handlers))
        for handler in self._handlers:
            try:
                await handler(event)
            except Exception:
                logger.exception("Handler %s failed on event %s", handler.__name__, event)

    async def stop(self) -> None:
        self._running = False
