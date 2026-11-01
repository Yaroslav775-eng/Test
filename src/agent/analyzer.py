"""
AI-агент: анализирует событие и предлагает конкретные действия.
Поддерживает Anthropic Claude и DeepSeek.
"""
import json
import logging
from dataclasses import dataclass

from ..dispatcher.event_types import Event, EventPriority

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """
Ты — проактивный ассистент по управлению рекламными кампаниями.
Тебе приходят события из разных каналов: Google Ads, Merchant Center, Email, Telegram, Viber.

Твоя задача:
1. Понять, что произошло и насколько это критично.
2. Предложить КОНКРЕТНЫЕ действия (не общие советы).
3. Указать, нужно ли подтверждение от владельца, или можно действовать автоматически.

Отвечай строго в JSON формате:
{
  "summary": "Краткое описание события (1-2 предложения)",
  "severity": "critical|high|medium|low",
  "recommended_actions": [
    {
      "action": "название действия",
      "description": "что именно сделать",
      "auto_apply": true/false,
      "confidence": 0.0-1.0
    }
  ],
  "requires_human_decision": true/false,
  "telegram_message": "Сообщение пользователю в Telegram (кратко, по делу)"
}

Примеры:
- Если ставка изменилась на 50% неизвестным пользователем — это HIGH, requires_human_decision=true
- Если товар отклонён из-за неправильного описания — HIGH, предложи конкретное исправление
- Если пришёл счёт на оплату — HIGH, telegram_message с суммой и сроком
- Если кампания приостановлена — CRITICAL, requires_human_decision=true
"""


@dataclass
class AnalysisResult:
    summary: str
    severity: str
    recommended_actions: list[dict]
    requires_human_decision: bool
    telegram_message: str
    raw_response: str = ""


class EventAnalyzer:
    def __init__(self, config: dict):
        self._cfg = config
        self._provider = config.get("provider", "anthropic")
        self._model = config.get("model", "claude-sonnet-4-6")

    async def analyze(self, event: Event) -> AnalysisResult:
        prompt = self._build_prompt(event)
        raw = await self._call_llm(prompt)
        return self._parse_response(raw, event)

    def _build_prompt(self, event: Event) -> str:
        return (
            f"Источник события: {event.source.value}\n"
            f"Тип события: {event.event_type}\n"
            f"Приоритет системы: {event.priority.value}\n"
            f"Время: {event.timestamp.isoformat()}\n"
            f"Данные события:\n{json.dumps(event.raw_data, ensure_ascii=False, indent=2)}\n\n"
            "Проанализируй событие и верни JSON с рекомендациями."
        )

    async def _call_llm(self, prompt: str) -> str:
        if self._provider == "anthropic":
            return await self._call_anthropic(prompt)
        elif self._provider == "deepseek":
            return await self._call_deepseek(prompt)
        raise ValueError(f"Unknown provider: {self._provider}")

    async def _call_anthropic(self, prompt: str) -> str:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=self._cfg["api_key"])
        message = await client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text

    async def _call_deepseek(self, prompt: str) -> str:
        import httpx
        headers = {
            "Authorization": f"Bearer {self._cfg['deepseek_api_key']}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self._cfg.get("model", "deepseek-chat"),
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.deepseek.com/chat/completions",
                headers=headers,
                json=payload,
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]

    def _parse_response(self, raw: str, event: Event) -> AnalysisResult:
        try:
            # Убираем markdown-обёртку если есть
            text = raw.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("Failed to parse LLM response as JSON: %s", raw[:200])
            data = {}

        fallback_msg = f"Событие: {event.source.value}/{event.event_type} (приоритет: {event.priority.value})"
        return AnalysisResult(
            summary=data.get("summary", fallback_msg),
            severity=data.get("severity", event.priority.value),
            recommended_actions=data.get("recommended_actions", []),
            requires_human_decision=data.get("requires_human_decision", True),
            telegram_message=data.get("telegram_message", fallback_msg),
            raw_response=raw,
        )
