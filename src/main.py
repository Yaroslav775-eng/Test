"""
Точка входа. Запускает все мониторы и HTTP-сервер параллельно.
"""
import asyncio
import logging
import os
from pathlib import Path

import uvicorn
import yaml

from .api.webhooks import app, setup as setup_webhooks
from .agent.analyzer import EventAnalyzer
from .agent.notifier import TelegramNotifier
from .channels.google_ads_monitor import GoogleAdsMonitor
from .channels.email_monitor import EmailMonitor
from .channels.merchant_monitor import MerchantMonitor
from .dispatcher.event_types import Event, EventPriority
from .dispatcher.reactor import Reactor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def load_config() -> dict:
    config_path = Path(os.getenv("CONFIG_PATH", "config/settings.yaml"))
    if not config_path.exists():
        raise FileNotFoundError(
            f"Config not found: {config_path}\n"
            "Copy config/settings.example.yaml → config/settings.yaml and fill in your keys."
        )
    with open(config_path) as f:
        return yaml.safe_load(f)


async def make_event_handler(analyzer: EventAnalyzer, notifier: TelegramNotifier):
    async def handle_event(event: Event) -> None:
        logger.info("Processing event: %s", event)
        try:
            analysis = await analyzer.analyze(event)
            logger.info(
                "Analysis done: severity=%s, requires_decision=%s",
                analysis.severity,
                analysis.requires_human_decision,
            )
            await notifier.notify(event, analysis)
        except Exception:
            logger.exception("Failed to process event %s", event)

    return handle_event


async def main() -> None:
    config = load_config()

    reactor = Reactor()
    analyzer = EventAnalyzer(config["agent"])
    notifier = TelegramNotifier(config["telegram"])

    handler = await make_event_handler(analyzer, notifier)
    reactor.add_handler(handler)

    setup_webhooks(reactor, config)

    tasks = [asyncio.create_task(reactor.run())]

    # Запускаем мониторы только если секция есть в конфиге
    if config.get("google_ads"):
        tasks.append(asyncio.create_task(
            GoogleAdsMonitor(config["google_ads"], reactor).run()
        ))

    if config.get("email"):
        tasks.append(asyncio.create_task(
            EmailMonitor(config["email"], reactor).run()
        ))

    if config.get("merchant_center"):
        tasks.append(asyncio.create_task(
            MerchantMonitor(config["merchant_center"], reactor).run()
        ))

    # HTTP сервер для вебхуков
    server_cfg = config.get("server", {})
    server = uvicorn.Server(uvicorn.Config(
        app,
        host=server_cfg.get("host", "0.0.0.0"),
        port=server_cfg.get("port", 8080),
        log_level="info",
    ))
    tasks.append(asyncio.create_task(server.serve()))

    logger.info("All components started. Waiting for events...")
    try:
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        await reactor.stop()


if __name__ == "__main__":
    asyncio.run(main())
