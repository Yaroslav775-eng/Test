"""
FastAPI сервер для приёма вебхуков от Telegram и Viber.
"""
import hashlib
import hmac
import json
import logging
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request, HTTPException, BackgroundTasks

from ..dispatcher.event_types import Event, EventSource, EventPriority

if TYPE_CHECKING:
    from ..dispatcher.reactor import Reactor

logger = logging.getLogger(__name__)
app = FastAPI(title="Ad Reactor Webhooks")

_reactor = None
_config = None


def setup(reactor, config: dict) -> None:
    global _reactor, _config
    _reactor = reactor
    _config = config


@app.post("/webhook/telegram")
async def telegram_webhook(request: Request, background_tasks: BackgroundTasks):
    """Принимает обновления от Telegram Bot API."""
    body = await request.body()
    data = json.loads(body)

    # Обрабатываем callback кнопок
    if "callback_query" in data:
        callback = data["callback_query"]
        logger.info("Telegram callback: %s", callback.get("data"))
        return {"ok": True}

    # Обычное сообщение
    message = data.get("message", {})
    text = message.get("text", "")
    from_user = message.get("from", {})

    if not text:
        return {"ok": True}

    event = Event(
        source=EventSource.TELEGRAM,
        event_type="message_received",
        raw_data={
            "text": text,
            "from_id": from_user.get("id"),
            "username": from_user.get("username"),
            "chat_id": message.get("chat", {}).get("id"),
        },
        priority=EventPriority.MEDIUM,
    )
    background_tasks.add_task(_reactor.push, event)
    return {"ok": True}


@app.post("/webhook/viber")
async def viber_webhook(request: Request, background_tasks: BackgroundTasks):
    """Принимает события от Viber через вебхук."""
    # Проверяем подпись Viber
    auth_token = _config.get("viber", {}).get("auth_token", "")
    body = await request.body()

    if auth_token:
        signature = request.headers.get("X-Viber-Content-Signature", "")
        expected = hmac.new(
            auth_token.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise HTTPException(status_code=403, detail="Invalid signature")

    data = json.loads(body)
    event_type = data.get("event")

    if event_type != "message":
        return {"status": 0}

    sender = data.get("sender", {})
    message = data.get("message", {})

    event = Event(
        source=EventSource.VIBER,
        event_type="message_received",
        raw_data={
            "text": message.get("text", ""),
            "sender_id": sender.get("id"),
            "sender_name": sender.get("name"),
            "message_type": message.get("type"),
        },
        priority=EventPriority.MEDIUM,
    )
    background_tasks.add_task(_reactor.push, event)
    return {"status": 0}


@app.get("/health")
async def health():
    return {"status": "ok", "reactor_running": _reactor is not None}
