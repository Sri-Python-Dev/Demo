"""Gmail API email provider.

Setup (one time):
  1. Create an OAuth "Desktop app" client in Google Cloud Console with the
     Gmail API enabled and save it as ``GMAIL_CREDENTIALS_FILE``.
  2. Run ``python -m app.email.gmail_client --authorize`` to complete the
     browser consent and write ``GMAIL_TOKEN_FILE``.

Neither file is ever committed (see .gitignore). Scope ``gmail.modify`` is
needed to label processed messages; use ``gmail.readonly`` if you only poll.
"""

from __future__ import annotations

import argparse
import base64
import logging
from pathlib import Path
from typing import Any

from app.email.base import EmailNotFoundError, EmailProvider

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


class GmailEmailProvider(EmailProvider):
    name = "gmail"

    def __init__(
        self,
        credentials_file: Path,
        token_file: Path,
        user_id: str = "me",
        query: str = "is:unread has:attachment filename:pdf",
        processed_label: str = "po-processed",
    ) -> None:
        self.credentials_file = Path(credentials_file)
        self.token_file = Path(token_file)
        self.user_id = user_id
        self.query = query
        self.processed_label = processed_label
        self._service: Any = None
        self._label_id: str | None = None

    # --------------------------------------------------------------- auth
    def _load_credentials(self):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        if not self.token_file.exists():
            raise RuntimeError(
                f"Gmail token not found at {self.token_file}. Run: python -m app.email.gmail_client --authorize"
            )
        creds = Credentials.from_authorized_user_file(str(self.token_file), SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            self.token_file.write_text(creds.to_json())
        return creds

    def authorize_interactively(self) -> None:
        from google_auth_oauthlib.flow import InstalledAppFlow

        flow = InstalledAppFlow.from_client_secrets_file(str(self.credentials_file), SCOPES)
        creds = flow.run_local_server(port=0)
        self.token_file.parent.mkdir(parents=True, exist_ok=True)
        self.token_file.write_text(creds.to_json())
        logger.info("Saved Gmail token to %s", self.token_file)

    @property
    def service(self):
        if self._service is None:
            from googleapiclient.discovery import build

            self._service = build("gmail", "v1", credentials=self._load_credentials(), cache_discovery=False)
        return self._service

    # ----------------------------------------------------------- provider
    def list_new_message_ids(self, limit: int = 50) -> list[str]:
        response = (
            self.service.users().messages().list(userId=self.user_id, q=self.query, maxResults=limit).execute()
        )
        return [m["id"] for m in response.get("messages", [])]

    def _get(self, message_id: str, fmt: str) -> dict:
        from googleapiclient.errors import HttpError

        try:
            return self.service.users().messages().get(userId=self.user_id, id=message_id, format=fmt).execute()
        except HttpError as exc:
            if getattr(exc, "status_code", None) == 404 or getattr(exc.resp, "status", None) == 404:
                raise EmailNotFoundError(f"Gmail message {message_id} not found") from exc
            raise  # 429/5xx etc. are transient -> Temporal retries

    def fetch_raw_message(self, message_id: str) -> bytes:
        # format=raw returns the complete RFC 822 message including attachments.
        message = self._get(message_id, "raw")
        return base64.urlsafe_b64decode(message["raw"].encode("ascii"))

    def get_thread_id(self, message_id: str) -> str | None:
        return self._get(message_id, "minimal").get("threadId")

    def _processed_label_id(self) -> str:
        if self._label_id is None:
            labels = self.service.users().labels().list(userId=self.user_id).execute().get("labels", [])
            existing = next((l for l in labels if l["name"] == self.processed_label), None)
            if existing is None:
                existing = (
                    self.service.users()
                    .labels()
                    .create(userId=self.user_id, body={"name": self.processed_label})
                    .execute()
                )
            self._label_id = existing["id"]
        return self._label_id

    def acknowledge(self, message_id: str) -> None:
        body = {"addLabelIds": [self._processed_label_id()], "removeLabelIds": ["UNREAD"]}
        self.service.users().messages().modify(userId=self.user_id, id=message_id, body=body).execute()
        logger.info("Labelled Gmail message %s as %s", message_id, self.processed_label)


def main() -> None:
    from app.config import get_settings

    parser = argparse.ArgumentParser(description="Gmail provider utilities")
    parser.add_argument("--authorize", action="store_true", help="run the OAuth consent flow")
    parser.add_argument("--list", action="store_true", help="list matching message ids")
    args = parser.parse_args()
    s = get_settings()
    provider = GmailEmailProvider(s.gmail_credentials_file, s.gmail_token_file, s.gmail_user_id, s.gmail_query)
    if args.authorize:
        provider.authorize_interactively()
    if args.list:
        print("\n".join(provider.list_new_message_ids()))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
