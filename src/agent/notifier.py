"""
Отправка уведомлений в Telegram с кнопками подтверждения.
"""
import json
import logging
from typing import TYPE_CHECKING

from ..dispatcher.event_types import Event, EventPriority
from .analyzer import AnalysisResult

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

PRIORITY_EMOJI = {
    EventPriority.CRITICAL: "🔴",
    EventPriority.HIGH: "🟠",
    EventPriority.MEDIUM: "🟡",
    EventPriority.LOW: "⚪",
}

SOURCE_EMOJI = {
    "google_ads": "📊",
    "merchant_center": "🛍️",
    "email": "📧",
    "telegram": "💬",
    "viber": "📱",
}


class TelegramNotifier:
    def __init__(self, config: dict):
        self._token = config["bot_token"]
        self._chat_id = config["admin_chat_id"]
        self._pending_actions: dict[str, dict] = {}  # message_id → {event, actions}

    async def notify(self, event: Event, analysis: AnalysisResult) -> None:
        try:
            import telegram
            bot = telegram.Bot(token=self._token)
        except ImportError:
            logger.error("python-telegram-bot not installed")
            return

        emoji = PRIORITY_EMOJI.get(event.priority, "⚪")
        source_emoji = SOURCE_EMOJI.get(event.source.value, "📌")
        text = self._format_message(event, analysis, emoji, source_emoji)

        if analysis.requires_human_decision and analysis.recommended_actions:
            keyboard = self._build_keyboard(analysis.recommended_actions)
            msg = await bot.send_message(
                chat_id=self._chat_id,
                text=text,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
            self._pending_actions[str(msg.message_id)] = {
                "event": event,
                "actions": analysis.recommended_actions,
            }
        else:
            await bot.send_message(
                chat_id=self._chat_id,
                text=text,
                parse_mode="HTML",
            )

    def _format_message(
        self,
        event: Event,
        analysis: AnalysisResult,
        emoji: str,
        source_emoji: str,
    ) -> str:
        actions_text = ""
        if analysis.recommended_actions:
            actions_text = "\n\n<b>Рекомендации:</b>\n"
            for i, action in enumerate(analysis.recommended_actions[:3], 1):
                auto = "✅ авто" if action.get("auto_apply") else "👆 вручную"
                actions_text += f"{i}. {action['action']} ({auto})\n   {action.get('description', '')}\n"

        return (
            f"{emoji} {source_emoji} <b>{event.source.value.upper()}</b>\n"
            f"{analysis.telegram_message}"
            f"{actions_text}"
        )

    def _build_keyboard(self, actions: list[dict]):
        try:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        except ImportError:
            return None

        buttons = []
        for i, action in enumerate(actions[:3]):
            buttons.append([
                InlineKeyboardButton(
                    f"✅ {action['action']}",
                    callback_data=json.dumps({"action_idx": i, "confirm": True})[:64],
                ),
                InlineKeyboardButton(
                    "❌ Пропустить",
                    callback_data=json.dumps({"action_idx": i, "confirm": False})[:64],
                ),
            ])
        return InlineKeyboardMarkup(buttons)

    async def handle_callback(self, callback_data: str, message_id: str) -> str:
        data = json.loads(callback_data)
        pending = self._pending_actions.get(message_id)
        if not pending:
            return "Действие устарело"
        action = pending["actions"][data["action_idx"]]
        if data["confirm"]:
            logger.info("User confirmed action: %s", action["action"])
            return f"✅ Выполняю: {action['action']}"
        return f"❌ Пропущено: {action['action']}"
