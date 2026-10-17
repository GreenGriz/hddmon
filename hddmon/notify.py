"""Уведомления: Telegram и email (опционально)."""

from __future__ import annotations

import json
import smtplib
import urllib.parse
import urllib.request
from email.message import EmailMessage

from .config import Config
from .models import Event, Verdict

SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}


def _events_to_text(events: list[Event]) -> str:
    lines = [f"[{e.severity}] {e.at.isoformat(timespec='seconds')} {e.message}" for e in events]
    return "\n".join(lines)


def send_telegram(cfg: Config, text: str) -> None:
    if not cfg.telegram.enabled or not cfg.telegram.token or not cfg.telegram.chat_id:
        return
    url = f"https://api.telegram.org/bot{cfg.telegram.token}/sendMessage"
    payload = json.dumps({"chat_id": cfg.telegram.chat_id, "text": text}).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        if resp.status != 200:
            raise RuntimeError(f"telegram HTTP {resp.status}")


def send_email(cfg: Config, subject: str, text: str) -> None:
    if not cfg.email.enabled or not cfg.email.smtp_host or not cfg.email.to_addrs:
        return
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.email.from_addr or cfg.email.username
    msg["To"] = ", ".join(cfg.email.to_addrs)
    msg.set_content(text)
    with smtplib.SMTP(cfg.email.smtp_host, cfg.email.smtp_port, timeout=30) as smtp:
        if cfg.email.starttls:
            smtp.starttls()
        if cfg.email.username:
            smtp.login(cfg.email.username, cfg.email.password)
        smtp.send_message(msg)


def notify_events(cfg: Config, events: list[Event], min_severity: str = "warning") -> list[str]:
    """Шлёт уведомления о событиях >= min_severity. Возвращает список ошибок."""
    threshold = SEVERITY_ORDER.get(min_severity, 1)
    important = [e for e in events if SEVERITY_ORDER.get(e.severity, 0) >= threshold]
    if not important:
        return []
    text = "hddmon:\n" + _events_to_text(important)
    errors: list[str] = []
    try:
        send_telegram(cfg, text)
    except Exception as e:
        errors.append(f"telegram: {e}")
    try:
        send_email(cfg, "hddmon: внимание требуется", text)
    except Exception as e:
        errors.append(f"email: {e}")
    return errors
