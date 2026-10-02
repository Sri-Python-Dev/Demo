"""Application configuration.

All settings come from environment variables (optionally loaded from a local
``.env`` file via python-dotenv). Nothing secret is ever hard-coded here.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Load .env from the project root if present. Real environment variables win.
load_dotenv(PROJECT_ROOT / ".env", override=False)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", case_sensitive=False, extra="ignore")

    # --- PostgreSQL -------------------------------------------------------
    database_url: str = Field(
        default="postgresql+psycopg://po_user:change-me@localhost:5432/po_demo",
        description="SQLAlchemy URL. Override via DATABASE_URL; never commit real passwords.",
    )
    database_echo: bool = False

    # --- Temporal ---------------------------------------------------------
    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "po-email-processing"
    # Optional mTLS / API-key auth for Temporal Cloud.
    temporal_api_key: str | None = None
    temporal_tls_cert_path: str | None = None
    temporal_tls_key_path: str | None = None

    # --- Email provider ---------------------------------------------------
    email_provider: Literal["local", "gmail"] = "local"
    local_inbox_dir: Path = PROJECT_ROOT / "sample_data" / "inbox"
    local_processed_dir: Path = PROJECT_ROOT / "var" / "processed_emails"

    gmail_credentials_file: Path = PROJECT_ROOT / "secrets" / "gmail_credentials.json"
    gmail_token_file: Path = PROJECT_ROOT / "secrets" / "gmail_token.json"
    gmail_user_id: str = "me"
    gmail_query: str = "is:unread has:attachment filename:pdf -label:po-processed"
    gmail_processed_label: str = "po-processed"

    poll_interval_seconds: int = 15

    # --- Storage / parsing -------------------------------------------------
    storage_dir: Path = PROJECT_ROOT / "var" / "storage"
    document_parser: Literal["docling", "pdfplumber"] = "docling"
    docling_do_ocr: bool = False
    # How to interpret ambiguous numeric dates like 03/04/2026.
    date_order: Literal["MDY", "DMY"] = "MDY"

    log_level: str = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
