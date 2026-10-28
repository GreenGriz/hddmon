import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hddmon import notify
from hddmon.config import Config
from hddmon.models import Event


def make_cfg() -> Config:
    cfg = Config()
    cfg.telegram.enabled = True
    cfg.telegram.token = "123:abc"
    cfg.telegram.chat_id = "42"
    cfg.email.enabled = True
    cfg.email.smtp_host = "smtp.example.com"
    cfg.email.smtp_port = 587
    cfg.email.starttls = True
    cfg.email.username = "user@example.com"
    cfg.email.password = "secret"
    cfg.email.from_addr = "hddmon@example.com"
    cfg.email.to_addrs = ["admin@example.com"]
    return cfg


def make_event(severity: str, message: str) -> Event:
    return Event(None, "test", severity, message)


class TestTelegram(unittest.TestCase):
    def test_disabled_does_not_send(self):
        cfg = make_cfg()
        cfg.telegram.enabled = False
        with mock.patch("hddmon.notify.urllib.request.urlopen") as urlopen:
            notify.send_telegram(cfg, "hello")
        urlopen.assert_not_called()

    def test_missing_credentials_raises(self):
        cfg = make_cfg()
        cfg.telegram.token = ""
        with self.assertRaisesRegex(ValueError, "token/chat_id"):
            notify.send_telegram(cfg, "hello")

    def test_sends_payload(self):
        cfg = make_cfg()
        response = mock.MagicMock()
        response.status = 200
        with mock.patch("hddmon.notify.urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value = response
            notify.send_telegram(cfg, "hello")
        req = urlopen.call_args.args[0]
        self.assertEqual(req.full_url, "https://api.telegram.org/bot123:abc/sendMessage")
        self.assertEqual(json.loads(req.data.decode()), {"chat_id": "42", "text": "hello"})
        urlopen.assert_called_once_with(req, timeout=30)

    def test_non_200_raises(self):
        cfg = make_cfg()
        response = mock.MagicMock()
        response.status = 500
        with mock.patch("hddmon.notify.urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value = response
            with self.assertRaisesRegex(RuntimeError, "HTTP 500"):
                notify.send_telegram(cfg, "hello")


class TestEmail(unittest.TestCase):
    def test_disabled_does_not_send(self):
        cfg = make_cfg()
        cfg.email.enabled = False
        with mock.patch("hddmon.notify.smtplib.SMTP") as smtp_cls:
            notify.send_email(cfg, "subject", "body")
        smtp_cls.assert_not_called()

    def test_missing_host_raises(self):
        cfg = make_cfg()
        cfg.email.smtp_host = ""
        with self.assertRaisesRegex(ValueError, "smtp_host"):
            notify.send_email(cfg, "subject", "body")

    def test_missing_recipients_raises(self):
        cfg = make_cfg()
        cfg.email.to_addrs = []
        with self.assertRaisesRegex(ValueError, "to_addrs"):
            notify.send_email(cfg, "subject", "body")

    def test_starttls_login_and_message(self):
        cfg = make_cfg()
        with mock.patch("hddmon.notify.smtplib.SMTP") as smtp_cls:
            notify.send_email(cfg, "тема", "тело")
        smtp_cls.assert_called_once_with("smtp.example.com", 587, timeout=30)
        smtp = smtp_cls.return_value.__enter__.return_value
        smtp.starttls.assert_called_once_with()
        smtp.login.assert_called_once_with("user@example.com", "secret")
        msg = smtp.send_message.call_args.args[0]
        self.assertEqual(msg["Subject"], "тема")
        self.assertEqual(msg["From"], "hddmon@example.com")
        self.assertEqual(msg["To"], "admin@example.com")
        self.assertIn("тело", msg.get_content())

    def test_ssl_uses_smtp_ssl(self):
        cfg = make_cfg()
        cfg.email.smtp_port = 465
        cfg.email.use_ssl = True
        with mock.patch("hddmon.notify.smtplib.SMTP") as smtp_cls, \
             mock.patch("hddmon.notify.smtplib.SMTP_SSL") as smtp_ssl_cls:
            notify.send_email(cfg, "тема", "тело")
        smtp_cls.assert_not_called()
        smtp_ssl_cls.assert_called_once_with("smtp.example.com", 465, timeout=30)
        smtp = smtp_ssl_cls.return_value.__enter__.return_value
        smtp.starttls.assert_not_called()

    def test_starttls_disabled(self):
        cfg = make_cfg()
        cfg.email.starttls = False
        with mock.patch("hddmon.notify.smtplib.SMTP") as smtp_cls:
            notify.send_email(cfg, "тема", "тело")
        smtp = smtp_cls.return_value.__enter__.return_value
        smtp.starttls.assert_not_called()

    def test_env_password_overrides_config(self):
        cfg = make_cfg()
        with mock.patch.dict("os.environ", {"HDDMON_SMTP_PASSWORD": "env-secret"}), \
             mock.patch("hddmon.notify.smtplib.SMTP") as smtp_cls:
            notify.send_email(cfg, "тема", "тело")
        smtp = smtp_cls.return_value.__enter__.return_value
        smtp.login.assert_called_once_with("user@example.com", "env-secret")

    def test_login_skipped_without_username(self):
        cfg = make_cfg()
        cfg.email.username = ""
        cfg.email.from_addr = "hddmon@example.com"
        with mock.patch("hddmon.notify.smtplib.SMTP") as smtp_cls:
            notify.send_email(cfg, "тема", "тело")
        smtp = smtp_cls.return_value.__enter__.return_value
        smtp.login.assert_not_called()


class TestNotifyEvents(unittest.TestCase):
    def test_info_events_are_not_sent(self):
        cfg = make_cfg()
        with mock.patch("hddmon.notify.send_telegram") as telegram, \
             mock.patch("hddmon.notify.send_email") as email:
            errors = notify.notify_events(cfg, [make_event("info", "просто информация")])
        self.assertEqual(errors, [])
        telegram.assert_not_called()
        email.assert_not_called()

    def test_warning_and_critical_are_sent(self):
        cfg = make_cfg()
        events = [
            make_event("info", "info"),
            make_event("warning", "warning"),
            make_event("critical", "critical"),
        ]
        with mock.patch("hddmon.notify.send_telegram") as telegram, \
             mock.patch("hddmon.notify.send_email") as email:
            errors = notify.notify_events(cfg, events)
        self.assertEqual(errors, [])
        telegram.assert_called_once()
        email.assert_called_once()
        text = telegram.call_args.args[1]
        self.assertIn("warning", text)
        self.assertIn("critical", text)
        self.assertNotIn("[info]", text)
        self.assertEqual(email.call_args.args[1], "hddmon: внимание требуется")

    def test_channel_errors_are_independent(self):
        cfg = make_cfg()
        with mock.patch("hddmon.notify.send_telegram", side_effect=RuntimeError("tg down")), \
             mock.patch("hddmon.notify.send_email", side_effect=OSError("smtp down")):
            errors = notify.notify_events(cfg, [make_event("warning", "warning")])
        self.assertEqual(errors, ["telegram: tg down", "email: smtp down"])

    def test_disabled_channel_is_skipped(self):
        cfg = make_cfg()
        cfg.telegram.enabled = False
        with mock.patch("hddmon.notify.send_telegram") as telegram, \
             mock.patch("hddmon.notify.send_email") as email:
            errors = notify.notify_events(cfg, [make_event("warning", "warning")])
        self.assertEqual(errors, [])
        telegram.assert_not_called()
        email.assert_called_once()


class TestNotifyTest(unittest.TestCase):
    def test_no_channels_returns_empty(self):
        cfg = make_cfg()
        cfg.telegram.enabled = False
        cfg.email.enabled = False
        self.assertEqual(notify.notify_test(cfg), [])

    def test_reports_success_per_channel(self):
        cfg = make_cfg()
        with mock.patch("hddmon.notify.send_telegram"), \
             mock.patch("hddmon.notify.send_email"):
            results = notify.notify_test(cfg)
        self.assertEqual([(r.channel, r.ok) for r in results], [("telegram", True), ("email", True)])

    def test_reports_failure_per_channel(self):
        cfg = make_cfg()
        with mock.patch("hddmon.notify.send_telegram", side_effect=RuntimeError("tg down")), \
             mock.patch("hddmon.notify.send_email"):
            results = notify.notify_test(cfg)
        self.assertEqual(
            [(r.channel, r.ok, r.error) for r in results],
            [("telegram", False, "tg down"), ("email", True, "")],
        )


if __name__ == "__main__":
    unittest.main()
