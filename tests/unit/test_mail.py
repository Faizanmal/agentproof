from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from agentproof_api.mail import send_mail, try_send_outbox_now
from agentproof_api.settings import Settings
from pydantic import SecretStr


def test_smtp_requires_starttls_and_never_enables_debug(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class SMTP:
        def __init__(self, host: str, port: int, timeout: int) -> None:
            assert host == "smtp.invalid" and port == 587 and timeout == 10

        def __enter__(self) -> "SMTP":
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def ehlo(self) -> None:
            calls.append("ehlo")

        def starttls(self, *, context: Any) -> None:
            assert context.check_hostname
            calls.append("tls")

        def login(self, username: str, password: str) -> None:
            assert calls == ["ehlo", "tls", "ehlo"]
            calls.append("login")

        def send_message(self, message: Any) -> None:
            assert message["To"] == "recipient@example.com"
            assert "https://owned.example/auth/verify#token=fixture" in message.get_content()
            calls.append("send")

    monkeypatch.setattr("agentproof_api.mail.smtplib.SMTP", SMTP)
    config = Settings(
        _env_file=None,
        mail_backend="smtp",
        smtp_host="smtp.invalid",
        smtp_user="service",
        smtp_password=SecretStr("test-only-placeholder"),
        mail_from="sender@example.com",
    )
    send_mail(
        config,
        "recipient@example.com",
        "verification",
        "https://owned.example/auth/verify#token=fixture",
    )
    assert calls == ["ehlo", "tls", "ehlo", "login", "send"]


def test_smtp_password_reset_subject(monkeypatch: pytest.MonkeyPatch) -> None:
    subjects: list[str] = []

    class SMTP:
        def __init__(self, host: str, port: int, timeout: int) -> None:
            pass

        def __enter__(self) -> "SMTP":
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def ehlo(self) -> None:
            pass

        def starttls(self, *, context: Any) -> None:
            pass

        def send_message(self, message: Any) -> None:
            subjects.append(message["Subject"])
            assert "auth/reset#token=fixture" in message.get_content()

    monkeypatch.setattr("agentproof_api.mail.smtplib.SMTP", SMTP)
    config = Settings(
        _env_file=None,
        mail_backend="smtp",
        smtp_host="smtp.invalid",
        mail_from="sender@example.com",
    )
    send_mail(
        config,
        "recipient@example.com",
        "password_reset",
        "https://owned.example/auth/reset#token=fixture",
    )
    assert subjects == ["Reset your AgentProof password"]


@pytest.mark.asyncio
async def test_try_send_outbox_now_marks_delivered(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[tuple[str, str]] = []

    def fake_send(config: Settings, recipient: str, purpose: str, link: str) -> None:
        sent.append((recipient, purpose))

    monkeypatch.setattr("agentproof_api.mail.send_mail", fake_send)
    session = MagicMock()
    session.execute = AsyncMock()
    config = Settings(
        _env_file=None,
        mail_backend="smtp",
        smtp_host="smtp.invalid",
        mail_from="sender@example.com",
    )
    outbox_id = uuid4()
    assert await try_send_outbox_now(
        session,
        config,
        outbox_id=outbox_id,
        recipient="user@example.com",
        purpose="password_reset",
        link="https://owned.example/auth/reset#token=fixture",
    )
    assert sent == [("user@example.com", "password_reset")]
    session.execute.assert_awaited()


@pytest.mark.asyncio
async def test_try_send_outbox_now_skips_development_backend() -> None:
    session = MagicMock()
    session.execute = AsyncMock()
    config = Settings(_env_file=None, mail_backend="development")
    assert not await try_send_outbox_now(
        session,
        config,
        outbox_id=uuid4(),
        recipient="user@example.com",
        purpose="password_reset",
        link="https://owned.example/auth/reset#token=fixture",
    )
    session.execute.assert_not_awaited()
