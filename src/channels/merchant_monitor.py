"""
Мониторинг проблем в Google Merchant Center.
Отслеживает отклонённые товары, нарушения политик, проблемы аккаунта.
"""
import asyncio
import logging
from typing import TYPE_CHECKING

from ..dispatcher.event_types import Event, EventSource, EventPriority

if TYPE_CHECKING:
    from ..dispatcher.reactor import Reactor

logger = logging.getLogger(__name__)


class MerchantMonitor:
    def __init__(self, config: dict, reactor: "Reactor"):
        self._cfg = config
        self._reactor = reactor
        self._known_issues: set[str] = set()

    async def run(self) -> None:
        interval = self._cfg.get("check_interval_seconds", 600)
        logger.info("Merchant Center monitor started, interval=%ds", interval)
        while True:
            try:
                await self._check_issues()
            except Exception:
                logger.exception("Merchant Center check failed")
            await asyncio.sleep(interval)

    async def _check_issues(self) -> None:
        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
        except ImportError:
            logger.warning("google-api-python-client not installed, skipping")
            return

        import json
        with open(self._cfg["credentials_file"]) as f:
            creds_data = json.load(f)

        creds = Credentials(
            token=creds_data.get("token"),
            refresh_token=creds_data.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=creds_data.get("client_id"),
            client_secret=creds_data.get("client_secret"),
        )

        service = build("content", "v2.1", credentials=creds)
        merchant_id = self._cfg["merchant_id"]
        watch_issues = set(self._cfg.get("watch_issues", ["disapproved"]))

        # Проверяем статус аккаунта
        account = service.accounts().get(
            merchantId=merchant_id, accountId=merchant_id
        ).execute()

        account_issues = account.get("accountStatus", {}).get("accountLevelIssues", [])
        for issue in account_issues:
            issue_id = f"account:{issue.get('id', '')}"
            if issue_id not in self._known_issues:
                severity = issue.get("severity", "warning")
                priority = EventPriority.CRITICAL if severity == "critical" else EventPriority.HIGH
                await self._reactor.push(Event(
                    source=EventSource.MERCHANT_CENTER,
                    event_type="account_issue",
                    raw_data={
                        "issue_id": issue.get("id"),
                        "title": issue.get("title"),
                        "severity": severity,
                        "documentation": issue.get("documentation"),
                    },
                    priority=priority,
                ))
                self._known_issues.add(issue_id)

        # Проверяем отклонённые товары
        if "disapproved" in watch_issues:
            products = service.productstatuses().list(
                merchantId=merchant_id,
                maxResults=50,
            ).execute()

            for item in products.get("resources", []):
                for dest in item.get("destinationStatuses", []):
                    if dest.get("approvalStatus") == "disapproved":
                        issue_id = f"product:{item['productId']}"
                        if issue_id not in self._known_issues:
                            await self._reactor.push(Event(
                                source=EventSource.MERCHANT_CENTER,
                                event_type="product_disapproved",
                                raw_data={
                                    "product_id": item["productId"],
                                    "title": item.get("title", ""),
                                    "destination": dest.get("destination"),
                                    "item_level_issues": item.get("itemLevelIssues", []),
                                },
                                priority=EventPriority.HIGH,
                            ))
                            self._known_issues.add(issue_id)

        logger.debug("Merchant Center check done")
