"""
Мониторинг изменений в Google Ads через Change Event API.
Каждые N секунд запрашивает журнал изменений и пушит важные события в Reactor.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from ..dispatcher.event_types import Event, EventSource, EventPriority

if TYPE_CHECKING:
    from ..dispatcher.reactor import Reactor

logger = logging.getLogger(__name__)


class GoogleAdsMonitor:
    def __init__(self, config: dict, reactor: "Reactor"):
        self._cfg = config
        self._reactor = reactor
        self._last_checked: datetime = datetime.now(timezone.utc) - timedelta(minutes=5)

    async def run(self) -> None:
        interval = self._cfg.get("check_interval_seconds", 300)
        logger.info("Google Ads monitor started, interval=%ds", interval)
        while True:
            try:
                await self._check_changes()
            except Exception:
                logger.exception("Google Ads check failed")
            await asyncio.sleep(interval)

    async def _check_changes(self) -> None:
        # Импорт здесь, чтобы не падало если библиотека не установлена
        try:
            from google.ads.googleads.client import GoogleAdsClient
        except ImportError:
            logger.warning("google-ads library not installed, skipping")
            return

        now = datetime.now(timezone.utc)
        window_start = self._last_checked.strftime("%Y-%m-%d %H:%M:%S")
        window_end = now.strftime("%Y-%m-%d %H:%M:%S")

        client = GoogleAdsClient.load_from_dict({
            "developer_token": self._cfg["developer_token"],
            "client_id": self._cfg["client_id"],
            "client_secret": self._cfg["client_secret"],
            "refresh_token": self._cfg["refresh_token"],
            "use_proto_plus": True,
        })

        ga_service = client.get_service("GoogleAdsService")
        customer_id = self._cfg["customer_id"]

        query = f"""
            SELECT
                change_event.change_date_time,
                change_event.change_resource_type,
                change_event.changed_fields,
                change_event.new_resource,
                change_event.old_resource,
                change_event.user_email
            FROM change_event
            WHERE change_event.change_date_time >= '{window_start}'
              AND change_event.change_date_time <= '{window_end}'
            ORDER BY change_event.change_date_time DESC
            LIMIT 100
        """

        response = ga_service.search(customer_id=customer_id, query=query)
        watch_types = set(self._cfg.get("watch_change_types", []))

        for row in response:
            change = row.change_event
            change_type = change.change_resource_type.name

            if watch_types and change_type not in watch_types:
                continue

            priority = self._assess_priority(change_type, change)
            event = Event(
                source=EventSource.GOOGLE_ADS,
                event_type=change_type.lower(),
                raw_data={
                    "change_type": change_type,
                    "changed_fields": list(change.changed_fields),
                    "user_email": change.user_email,
                    "change_time": change.change_date_time,
                    "new_resource": str(change.new_resource),
                    "old_resource": str(change.old_resource),
                },
                priority=priority,
            )
            await self._reactor.push(event)

        self._last_checked = now
        logger.debug("Google Ads check done: window %s — %s", window_start, window_end)

    def _assess_priority(self, change_type: str, change) -> EventPriority:
        critical_types = {"CAMPAIGN_STATUS", "AD_GROUP_AD_STATUS"}
        high_types = {"CAMPAIGN_BUDGET", "AD_GROUP_BID_MODIFIER"}
        if change_type in critical_types:
            return EventPriority.CRITICAL
        if change_type in high_types:
            return EventPriority.HIGH
        return EventPriority.MEDIUM
