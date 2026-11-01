from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class EventSource(str, Enum):
    TELEGRAM = "telegram"
    VIBER = "viber"
    EMAIL = "email"
    GOOGLE_ADS = "google_ads"
    MERCHANT_CENTER = "merchant_center"


class EventPriority(str, Enum):
    CRITICAL = "critical"   # немедленно, блокирует работу
    HIGH = "high"           # важно, нужен ответ сегодня
    MEDIUM = "medium"       # стоит посмотреть
    LOW = "low"             # информационное


@dataclass
class Event:
    source: EventSource
    event_type: str          # например: "bid_changed", "email_received", "product_disapproved"
    raw_data: dict[str, Any]
    timestamp: datetime = field(default_factory=datetime.utcnow)
    priority: EventPriority = EventPriority.MEDIUM
    metadata: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"[{self.source.value}:{self.event_type}] priority={self.priority.value}"
