"""Shared pytest fixtures.

* PostgreSQL tests use TEST_DATABASE_URL (default: local po_demo_test) and are
  skipped automatically if the database is unreachable.
* Temporal tests start an embedded Temporal dev server via the SDK test
  environment. If the ``temporal`` CLI is on PATH (or TEMPORAL_CLI_PATH is set)
  that binary is used; otherwise the SDK downloads one on first use.
* Real Docling conversion tests need the Docling models (downloaded from
  Hugging Face on first use) and only run with RUN_DOCLING_TESTS=1.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SAMPLE = ROOT / "sample_data"
TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL", "postgresql+psycopg://po_user:po_password@localhost:5432/po_demo_test"
)

run_docling = pytest.mark.skipif(
    os.getenv("RUN_DOCLING_TESTS") != "1", reason="set RUN_DOCLING_TESTS=1 to run real Docling conversion"
)


@pytest.fixture(scope="session")
def sample_dir() -> Path:
    return SAMPLE


@pytest.fixture
def pdfplumber_parser():
    from app.extraction.pdfplumber_parser import PdfPlumberParser

    return PdfPlumberParser()


@pytest.fixture
def po_extractor():
    from app.extraction.po_extractor import RuleBasedPurchaseOrderExtractor

    return RuleBasedPurchaseOrderExtractor()


# ---------------------------------------------------------------- PostgreSQL
@pytest.fixture(scope="session")
def db_engine():
    from sqlalchemy import text

    from app.database.connection import make_engine

    engine = make_engine(TEST_DATABASE_URL)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"PostgreSQL not reachable at TEST_DATABASE_URL: {exc}")

    # Build the schema through the real Alembic migrations (tests them too).
    from scripts.init_db import alembic_config, reset

    from alembic import command

    reset(TEST_DATABASE_URL)
    command.upgrade(alembic_config(TEST_DATABASE_URL), "head")
    yield engine
    engine.dispose()


@pytest.fixture
def repository(db_engine):
    from sqlalchemy import text

    from app.database.repository import PurchaseOrderRepository

    with db_engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE processing_events, purchase_order_items, purchase_orders, documents, inbound_emails "
                "RESTART IDENTITY CASCADE"
            )
        )
    return PurchaseOrderRepository(db_engine)


# ------------------------------------------------------------------ Temporal
def temporal_cli_path() -> str | None:
    return os.getenv("TEMPORAL_CLI_PATH") or shutil.which("temporal")


@pytest.fixture(scope="session")
async def temporal_env():
    from temporalio.contrib.pydantic import pydantic_data_converter
    from temporalio.testing import WorkflowEnvironment

    kwargs = {"data_converter": pydantic_data_converter}
    if cli := temporal_cli_path():
        kwargs["dev_server_existing_path"] = cli
    try:
        env = await WorkflowEnvironment.start_local(**kwargs)
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"could not start Temporal dev server: {exc}")
    yield env
    await env.shutdown()
