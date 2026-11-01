"""
Мониторинг почты через IMAP.
Периодически проверяет новые письма и пушит важные в Reactor.
"""
import asyncio
import email
import imaplib
import logging
from email.header import decode_header

from ..dispatcher.event_types import Event, EventSource, EventPriority

logger = logging.getLogger(__name__)

CRITICAL_SUBJECTS = ["suspended", "disabled", "urgent", "policy violation"]
HIGH_SUBJECTS = ["billing", "invoice", "payment", "alert", "disapproved"]


def _decode_str(value: str | bytes, charset: str | None = None) -> str:
    if isinstance(value, bytes):
        return value.decode(charset or "utf-8", errors="replace")
    return value


def _parse_subject(raw_subject: str) -> str:
    parts = decode_header(raw_subject)
    return " ".join(_decode_str(text, enc) for text, enc in parts)


class EmailMonitor:
    def __init__(self, config: dict, reactor):
        self._cfg = config
        self._reactor = reactor
        self._seen_uids: set[str] = set()

    async def run(self) -> None:
        interval = self._cfg.get("check_interval_seconds", 60)
        logger.info("Email monitor started, interval=%ds", interval)
        while True:
            try:
                await asyncio.to_thread(self._check_mail)
            except Exception:
                logger.exception("Email check failed")
            await asyncio.sleep(interval)

    def _check_mail(self) -> None:
        cfg = self._cfg
        conn = imaplib.IMAP4_SSL(cfg["imap_host"], cfg.get("imap_port", 993))
        conn.login(cfg["username"], cfg["password"])

        for folder in cfg.get("folders", ["INBOX"]):
            conn.select(folder)
            _, data = conn.search(None, "UNSEEN")
            uids = data[0].split()

            for uid in uids:
                uid_str = uid.decode()
                if uid_str in self._seen_uids:
                    continue

                _, msg_data = conn.fetch(uid, "(RFC822)")
                raw = msg_data[0][1]
                msg = email.message_from_bytes(raw)

                subject = _parse_subject(msg.get("Subject", ""))
                sender = msg.get("From", "")
                body = self._extract_body(msg)

                priority = self._assess_priority(subject, sender)
                important_senders = cfg.get("important_senders", [])
                important_subjects = cfg.get("important_subjects", [])

                sender_match = any(s.lower() in sender.lower() for s in important_senders)
                subject_match = any(s.lower() in subject.lower() for s in important_subjects + HIGH_SUBJECTS + CRITICAL_SUBJECTS)

                if not important_senders and not important_subjects:
                    # Если фильтры не заданы — пропускать всё HIGH и выше
                    if priority in (EventPriority.HIGH, EventPriority.CRITICAL):
                        pass  # продолжаем
                    else:
                        self._seen_uids.add(uid_str)
                        continue
                elif not (sender_match or subject_match):
                    self._seen_uids.add(uid_str)
                    continue

                asyncio.get_event_loop().call_soon_threadsafe(
                    asyncio.ensure_future,
                    self._reactor.push(Event(
                        source=EventSource.EMAIL,
                        event_type="email_received",
                        raw_data={
                            "subject": subject,
                            "from": sender,
                            "body_preview": body[:500],
                            "folder": folder,
                        },
                        priority=priority,
                    ))
                )
                self._seen_uids.add(uid_str)

        conn.logout()

    def _extract_body(self, msg: email.message.Message) -> str:
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    payload = part.get_payload(decode=True)
                    return _decode_str(payload, part.get_content_charset())
        else:
            payload = msg.get_payload(decode=True)
            return _decode_str(payload, msg.get_content_charset())
        return ""

    def _assess_priority(self, subject: str, sender: str) -> EventPriority:
        subj_lower = subject.lower()
        if any(kw in subj_lower for kw in CRITICAL_SUBJECTS):
            return EventPriority.CRITICAL
        if any(kw in subj_lower for kw in HIGH_SUBJECTS):
            return EventPriority.HIGH
        return EventPriority.MEDIUM
