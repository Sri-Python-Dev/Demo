"""Create/upgrade the PostgreSQL schema via Alembic (``alembic upgrade head``).

    python scripts/init_db.py            # apply migrations
    python scripts/init_db.py --reset    # DROP all demo tables first (demo only!)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.database.connection import make_engine  # noqa: E402


def alembic_config(database_url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return cfg


def reset(database_url: str) -> None:
    engine = make_engine(database_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "DROP TABLE IF EXISTS processing_events, purchase_order_items, purchase_orders, "
                "documents, inbound_emails, alembic_version CASCADE"
            )
        )
    engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="drop all tables before migrating (demo only)")
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args()
    url = args.database_url or get_settings().database_url
    if args.reset:
        reset(url)
        print("Dropped existing tables")
    command.upgrade(alembic_config(url), "head")
    print("Database schema is up to date")


if __name__ == "__main__":
    main()
