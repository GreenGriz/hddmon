"""Уведомления: Telegram и email (опционально)."""

from __future__ import annotations

import json
import os
import smtplib
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage

from .config import Config
from .models import Event

SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}


@dataclass(frozen=True)
class DeliveryResult:
    """Результат попытки отправки в один канал."""

    channel: str
    ok: bool
    error: str = ""


def _events_to_text(events: list[Event]) -> str:
    lines = [f"[{e.severity}] {e.at.isoformat(timespec='seconds')} {e.message}" for e in events]
    return "\n".join(lines)


def send_telegram(cfg: Config, text: str) -> None:
    """Отправить текст в Telegram. Выключенный канал — no-op."""
    if not cfg.telegram.enabled:
        return
    if not cfg.telegram.token or not cfg.telegram.chat_id:
        raise ValueError("не заданы token/chat_id")
    url = f"https://api.telegram.org/bot{cfg.telegram.token}/sendMessage"
    payload = json.dumps({"chat_id": cfg.telegram.chat_id, "text": text}).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        if resp.status != 200:
            raise RuntimeError(f"telegram HTTP {resp.status}")


def send_email(cfg: Config, subject: str, text: str) -> None:
    """Отправить письмо. Выключенный канал — no-op.

    Пароль можно не хранить в конфиге, а задать в переменной окружения
    HDDMON_SMTP_PASSWORD (например, через systemd EnvironmentFile).
    """
    if not cfg.email.enabled:
        return
    if not cfg.email.smtp_host:
        raise ValueError("не задан smtp_host")
    if not cfg.email.to_addrs:
        raise ValueError("не задан to_addrs")
    sender = cfg.email.from_addr or cfg.email.username
    if not sender:
        raise ValueError("не задан from_addr/username")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(cfg.email.to_addrs)
    msg.set_content(text)

    password = os.environ.get("HDDMON_SMTP_PASSWORD") or cfg.email.password
    smtp_cls = smtplib.SMTP_SSL if cfg.email.use_ssl else smtplib.SMTP
    with smtp_cls(cfg.email.smtp_host, cfg.email.smtp_port, timeout=30) as smtp:
        if cfg.email.starttls and not cfg.email.use_ssl:
            smtp.starttls()
        if cfg.email.username:
            smtp.login(cfg.email.username, password)
        smtp.send_message(msg)


def _deliver(cfg: Config, subject: str, text: str) -> list[DeliveryResult]:
    """Разослать сообщение во все включённые каналы, не прерываясь на ошибках."""
    results: list[DeliveryResult] = []
    if cfg.telegram.enabled:
        try:
            send_telegram(cfg, text)
            results.append(DeliveryResult("telegram", True))
        except Exception as e:
            results.append(DeliveryResult("telegram", False, str(e)))
    if cfg.email.enabled:
        try:
            send_email(cfg, subject, text)
            results.append(DeliveryResult("email", True))
        except Exception as e:
            results.append(DeliveryResult("email", False, str(e)))
    return results


def notify_events(cfg: Config, events: list[Event], min_severity: str = "warning") -> list[str]:
    """Шлёт уведомления о событиях >= min_severity. Возвращает список ошибок."""
    threshold = SEVERITY_ORDER.get(min_severity, 1)
    important = [e for e in events if SEVERITY_ORDER.get(e.severity, 0) >= threshold]
    if not important:
        return []
    text = "hddmon:\n" + _events_to_text(important)
    results = _deliver(cfg, "hddmon: внимание требуется", text)
    return [f"{r.channel}: {r.error}" for r in results if not r.ok]


def notify_test(cfg: Config) -> list[DeliveryResult]:
    """Отправить тестовое сообщение во все включённые каналы.

    Возвращает по одному результату на включённый канал. Если ничего не
    включено — пустой список.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    text = (
        "hddmon: тестовое уведомление\n"
        f"{now}\n"
        "Если вы получили это сообщение, канал настроен правильно."
    )
    return _deliver(cfg, "hddmon: тестовое уведомление", text)
