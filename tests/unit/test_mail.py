from typing import Any

import pytest
from agentproof_api.mail import send_verification
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
    send_verification(
        config, "recipient@example.com", "https://owned.example/auth/verify#token=fixture"
    )
    assert calls == ["ehlo", "tls", "ehlo", "login", "send"]
