from __future__ import annotations

from app.config import Settings, get_settings
from app.email.base import EmailProvider


def get_email_provider(settings: Settings | None = None) -> EmailProvider:
    s = settings or get_settings()
    if s.email_provider == "local":
        from app.email.local_provider import LocalDirectoryEmailProvider

        return LocalDirectoryEmailProvider(s.local_inbox_dir, s.local_processed_dir)
    if s.email_provider == "gmail":
        from app.email.gmail_client import GmailEmailProvider

        return GmailEmailProvider(
            s.gmail_credentials_file, s.gmail_token_file, s.gmail_user_id, s.gmail_query, s.gmail_processed_label
        )
    raise ValueError(f"Unknown EMAIL_PROVIDER {s.email_provider!r}")
