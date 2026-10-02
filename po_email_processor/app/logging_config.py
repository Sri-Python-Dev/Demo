from __future__ import annotations

import logging


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    # Docling/pdfminer and friends are chatty at INFO.
    for noisy in ("pdfminer", "docling", "httpx", "urllib3", "PIL", "rapidocr"):
        logging.getLogger(noisy).setLevel(max(logging.WARNING, logging.getLogger().level))
