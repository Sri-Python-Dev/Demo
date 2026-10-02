"""Workflow starter and email listener.

Examples:
    # Demo: "send" a sample email into the local inbox and process it
    python start_workflow.py --eml sample_data/emails/01_acme_po.eml --wait

    # Process one specific provider message id
    python start_workflow.py --message-id "po-100245.acme@mail.acme-mfg.example.com" --wait

    # Listener: poll the configured provider (local folder or Gmail) forever
    python start_workflow.py --poll

    # Re-deliver an already processed email to demonstrate DB-level idempotency
    python start_workflow.py --eml sample_data/emails/01_acme_po.eml --wait --allow-rerun

The listener only *starts* workflows; all email/document/database work happens
in Temporal activities. The workflow id is derived from the provider message
id, so the same email can never run twice concurrently (first idempotency layer;
the database unique keys are the second).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import re
import sys
from pathlib import Path

from temporalio.client import Client, WorkflowExecutionStatus, WorkflowFailureError
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from app.config import get_settings
from app.email.factory import get_email_provider
from app.email.local_provider import LocalDirectoryEmailProvider
from app.logging_config import configure_logging
from app.models.schemas import WorkflowInput, WorkflowResult
from app.temporal_client import connect
from app.workflows.purchase_order_workflow import PurchaseOrderEmailWorkflow

logger = logging.getLogger("listener")


def workflow_id_for(provider: str, message_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._@-]+", "_", message_id)[:120]
    digest = hashlib.sha256(message_id.encode()).hexdigest()[:8]
    return f"po-email-{provider}-{safe}-{digest}"


async def start_one(client: Client, provider: str, message_id: str, *, wait: bool, allow_rerun: bool) -> dict:
    settings = get_settings()
    wf_id = workflow_id_for(provider, message_id)
    policy = WorkflowIDReusePolicy.ALLOW_DUPLICATE if allow_rerun else WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY
    try:
        handle = await client.start_workflow(
            PurchaseOrderEmailWorkflow.run,
            WorkflowInput(provider=provider, provider_message_id=message_id),
            id=wf_id,
            task_queue=settings.temporal_task_queue,
            id_reuse_policy=policy,
        )
        logger.info("Started workflow %s for message %s", wf_id, message_id)
        duplicate_event = False
    except WorkflowAlreadyStartedError:
        logger.info("Workflow %s already exists for message %s (duplicate event ignored)", wf_id, message_id)
        handle = client.get_workflow_handle_for(PurchaseOrderEmailWorkflow.run, wf_id)
        duplicate_event = True
        if not wait:
            return {"status": "ALREADY_STARTED", "workflow_id": wf_id}

    if not wait:
        return {"status": "STARTED", "workflow_id": wf_id}
    # For a duplicate event this is the result of the existing (earlier) workflow run.
    extra = {"duplicate_event": True, "note": "result of the existing workflow run"} if duplicate_event else {}
    try:
        result: WorkflowResult = await handle.result()
        return {"workflow_id": wf_id, **extra, **result.model_dump(mode="json")}
    except WorkflowFailureError as err:
        cause = err.cause
        while getattr(cause, "cause", None) is not None:
            cause = cause.cause
        return {"status": "FAILED", "workflow_id": wf_id, **extra, "error": str(cause or err)}


async def poll(client: Client, *, once: bool, wait: bool) -> None:
    settings = get_settings()
    provider = get_email_provider(settings)
    logger.info("Polling %s provider every %ss", provider.name, settings.poll_interval_seconds)
    while True:
        try:
            ids = await asyncio.to_thread(provider.list_new_message_ids)
            for message_id in ids:
                result = await start_one(client, provider.name, message_id, wait=wait, allow_rerun=False)
                print(json.dumps(result, indent=2))
                if result.get("duplicate_event") or result["status"] == "ALREADY_STARTED":
                    # Re-delivered message whose workflow already finished: acknowledge it so it
                    # isn't picked up again on every poll. Running workflows acknowledge themselves.
                    desc = await client.get_workflow_handle(result["workflow_id"]).describe()
                    if desc.status not in (None, WorkflowExecutionStatus.RUNNING):
                        await asyncio.to_thread(provider.acknowledge, message_id)
                        logger.info("Acknowledged re-delivered message %s", message_id)
        except Exception:  # keep the listener alive; Temporal handles per-message retries
            logger.exception("Polling iteration failed")
        if once:
            return
        await asyncio.sleep(settings.poll_interval_seconds)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--eml", nargs="+", type=Path, help="deliver .eml file(s) to the local inbox and process them")
    group.add_argument("--message-id", help="start the workflow for one provider message id")
    group.add_argument("--poll", action="store_true", help="poll the configured provider for new emails")
    parser.add_argument("--once", action="store_true", help="with --poll: single polling pass")
    parser.add_argument("--wait", action="store_true", help="wait for and print workflow results")
    parser.add_argument("--allow-rerun", action="store_true", help="allow re-running a completed workflow id")
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level)
    client = await connect(settings)

    if args.poll:
        await poll(client, once=args.once, wait=args.wait)
        return 0

    results = []
    if args.eml:
        if settings.email_provider != "local":
            parser.error("--eml requires EMAIL_PROVIDER=local")
        provider = get_email_provider(settings)
        assert isinstance(provider, LocalDirectoryEmailProvider)
        for path in args.eml:
            message_id = provider.deliver(path)
            logger.info("Delivered %s to %s as message %s", path.name, provider.inbox_dir, message_id)
            results.append(await start_one(client, provider.name, message_id, wait=args.wait, allow_rerun=args.allow_rerun))
    else:
        results.append(
            await start_one(client, settings.email_provider, args.message_id, wait=args.wait, allow_rerun=args.allow_rerun)
        )

    for result in results:
        print(json.dumps(result, indent=2))
    return 0 if all(r.get("status") != "FAILED" for r in results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
