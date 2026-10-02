"""Temporal client factory (shared by the worker and the workflow starter)."""

from __future__ import annotations

from pathlib import Path

from temporalio.client import Client, TLSConfig
from temporalio.contrib.pydantic import pydantic_data_converter

from app.config import Settings, get_settings


async def connect(settings: Settings | None = None) -> Client:
    s = settings or get_settings()
    tls: TLSConfig | bool = False
    if s.temporal_tls_cert_path and s.temporal_tls_key_path:
        tls = TLSConfig(
            client_cert=Path(s.temporal_tls_cert_path).read_bytes(),
            client_private_key=Path(s.temporal_tls_key_path).read_bytes(),
        )
    elif s.temporal_api_key:
        tls = True
    return await Client.connect(
        s.temporal_address,
        namespace=s.temporal_namespace,
        api_key=s.temporal_api_key,
        tls=tls,
        # Pydantic models (Decimal, date, datetime, enums) round-trip through Temporal.
        data_converter=pydantic_data_converter,
    )
