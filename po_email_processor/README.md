# Email → Purchase Order Extraction (Temporal + Docling + PostgreSQL)

A demo/POC with production-style structure: when an email carrying a Purchase Order PDF arrives, a
Temporal workflow fetches it, captures the email metadata, stores the attachment, parses it with
**Docling**, extracts the PO header and all line items into **Pydantic** models, validates and
normalises them, and persists everything to **PostgreSQL** — with retries, timeouts, idempotency,
status tracking and full visibility in the Temporal UI.

* Design, ER diagram, activity boundaries and assumptions: **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**
* Mail sources: a local `.eml` folder (demo) or Gmail, behind one `EmailProvider` interface — the workflow is identical.

```
email ─► fetch_email ─► extract_email_metadata ─► register_email
           └─► per PDF: download_po_attachment ─► register_document ─► parse_document_with_docling
                ─► extract_purchase_order ─► validate_purchase_order ─► save_purchase_order_to_postgres
      ─► update_processing_status ─► acknowledge_email
```

## Prerequisites

* Python 3.11+
* Docker (for PostgreSQL) — or any PostgreSQL 14+
* Temporal CLI (local dev server + UI): `brew install temporal` or `curl -sSf https://temporal.download/cli.sh | sh`
* Internet access on the first Docling run (it downloads its layout/table models from Hugging Face, ~0.5 GB, cached afterwards)

## Setup

```bash
cd po_email_processor
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt                     # runtime + test deps

cp .env.example .env
# edit .env: set the password in DATABASE_URL / TEST_DATABASE_URL to match POSTGRES_PASSWORD below
```

## Run the demo

Use four terminals (all from `po_email_processor/` with the venv active).

**1. Start PostgreSQL** and create the schema (Alembic migrations):

```bash
export POSTGRES_PASSWORD=po_password        # demo only; must match DATABASE_URL in .env
docker compose up -d postgres
python scripts/init_db.py                   # alembic upgrade head  (add --reset to start clean)
```

**2. Start Temporal** (gRPC on `localhost:7233`, Web UI on <http://localhost:8233>):

```bash
temporal server start-dev --db-filename temporal.db
# or, without the CLI:  docker compose --profile temporal up -d temporal
```

**3. Start the worker:**

```bash
python worker.py
```

**4. "Send" sample PO emails** — each `.eml` is dropped into the local inbox and a workflow is started for it:

```bash
# A normal PO with 5 line items
python start_workflow.py --eml sample_data/emails/01_acme_po.eml --wait
```

```json
{
  "workflow_id": "po-email-local-po-100245.acme@mail.acme-mfg.example.com-2a31e953",
  "status": "COMPLETED",
  "email_id": "0853a352-...",
  "document_id": "ae559a16-...",
  "purchase_order_id": "7fbbc6ee-...",
  "po_number": "PO-100245",
  "line_items_processed": 5,
  "documents": [ { "filename": "acme_PO-100245.pdf", "status": "SAVED", ... } ]
}
```

Then the other scenarios:

```bash
python start_workflow.py --eml sample_data/emails/02_globex_po_multipage.eml --wait   # 2 pages, 28 lines, EUR, other labels
python start_workflow.py --eml sample_data/emails/03_northwind_minimal_po.eml --wait  # missing optional fields -> null
python start_workflow.py --eml sample_data/emails/04_acme_po_resent.eml --wait        # same PDF, new email -> DUPLICATE
python start_workflow.py --eml sample_data/emails/05_malformed_pdf.eml --wait         # corrupt PDF -> FAILED, not retried
python start_workflow.py --eml sample_data/emails/06_no_attachment.eml --wait         # no PDF -> IGNORED

# Duplicate event for an email already processed:
python start_workflow.py --eml sample_data/emails/01_acme_po.eml --wait               # rejected by Temporal (same workflow id)
python start_workflow.py --eml sample_data/emails/01_acme_po.eml --wait --allow-rerun # new run -> DB idempotency -> DUPLICATE
```

Or run the **email listener** and just drop files into the inbox:

```bash
python start_workflow.py --poll                         # polls LOCAL_INBOX_DIR every POLL_INTERVAL_SECONDS
cp sample_data/emails/02_globex_po_multipage.eml sample_data/inbox/
```

**5. Show the workflow in the Temporal UI:** open <http://localhost:8233> → *Workflows*. Each email is one
`PurchaseOrderEmailWorkflow` execution; open it to see every activity, its input/output, attempts,
and (for `05_malformed_pdf`) the non-retryable `DocumentParseError`. From the CLI:

```bash
temporal workflow list
temporal workflow query --workflow-id <id> --type progress
```

**6. Query PostgreSQL:**

```bash
python scripts/show_results.py                     # emails / documents / POs summary (no psql needed)
python scripts/show_results.py PO-100245           # one PO: header + all line items

docker compose exec -T postgres psql -U po_user -d po_demo < sql/verify_queries.sql
```

`sql/verify_queries.sql` shows: email metadata and statuses, documents with SHA-256 hashes, PO
headers, the line items of PO-100245, per-PO reconciliation (sum of lines vs subtotal), the
email → document → PO lineage with workflow ids, idempotency checks (must return 0 rows), the audit
trail, failures with error details, and the stored extraction JSONB. A few examples:

```sql
-- PO headers
SELECT po_number, supplier_name, buyer_name, order_date, currency, subtotal, tax, shipping, total_amount
FROM purchase_orders ORDER BY order_date;

-- All line items of one PO
SELECT i.line_number, i.part_number, i.part_name, i.quantity, i.unit_of_measure, i.unit_price, i.line_total
FROM purchase_order_items i JOIN purchase_orders p ON p.id = i.purchase_order_id
WHERE p.po_number = 'PO-100245' ORDER BY i.sequence;

-- Email metadata that produced each PO
SELECT e.sender_email, e.subject, e.received_at, d.filename, p.po_number, e.workflow_id
FROM purchase_orders p JOIN documents d ON d.id = p.document_id JOIN inbound_emails e ON e.id = p.email_id;

-- No duplicates were created
SELECT po_number, count(*) FROM purchase_orders GROUP BY po_number, supplier_name HAVING count(*) > 1;
```

Expected result after all six emails: 3 POs (5 + 28 + 2 = 35 line items), email statuses
`SAVED ×3, DUPLICATE, FAILED, IGNORED`, and no duplicate rows.

## Using Gmail instead of the local folder

1. In Google Cloud Console enable the Gmail API and create an OAuth client of type *Desktop app*;
   save the JSON as `secrets/gmail_credentials.json` (git-ignored).
2. Authorise once (opens a browser, writes `secrets/gmail_token.json`):
   `python -m app.email.gmail_client --authorize`
3. Set `EMAIL_PROVIDER=gmail` in `.env` (optionally adjust `GMAIL_QUERY`), restart the worker, and run
   `python start_workflow.py --poll`.

Messages are fetched with `format=raw` (the full RFC 822 message), so the same MIME parsing and the
same workflow are used. Processed messages get the `po-processed` label and are marked read.

## Tests

```bash
pytest                         # 75 tests: unit, PostgreSQL, Temporal workflow, end-to-end
RUN_DOCLING_TESTS=1 pytest     # + real Docling conversion of the sample PDFs (downloads models)
```

* PostgreSQL tests use `TEST_DATABASE_URL` (a separate database the tests wipe; the docker-compose
  Postgres creates `po_demo_test` automatically) and are skipped if it is unreachable.
* Temporal tests start an embedded dev server via the SDK test environment (using the `temporal` CLI
  if it is on `PATH`, otherwise the SDK downloads one).

| Area | Tests |
|---|---|
| Email metadata extraction | `test_email_metadata.py` |
| Docling parsing (mapping + real conversion) | `test_docling_parser.py` |
| PO extraction, multiple line items, multi-page, missing fields, alternative labels | `test_po_extractor.py` |
| Malformed documents | `test_po_extractor.py`, `test_activities.py`, `test_workflow.py` |
| Validation / normalisation | `test_validation.py`, `test_normalization.py` |
| Duplicate email, duplicate attachment, duplicate PO, persistence, migrations | `test_repository.py`, `test_workflow.py` |
| Activity failure / retry behaviour | `test_activities.py`, `test_workflow.py` |

## Golden dataset

`sample_data/expected/*.json` hold the correct extraction for each sample PDF (generated from the same
source data as the PDFs, not from extractor output). Compare the extractor against them:

```bash
python scripts/evaluate_golden.py --show-mismatches              # parser from DOCUMENT_PARSER
python scripts/evaluate_golden.py --parser docling --min-accuracy 0.95
```

To grow the dataset, add a PDF plus an expected JSON with the same structure; missing values must be
`null` (a hallucinated value counts as an error).

## Configuration

All settings are environment variables (see `.env.example`); no credentials are hard-coded.
Notable ones: `DOCUMENT_PARSER` (`docling` default, `pdfplumber` fallback for networks where Docling
models can't be downloaded), `DOCLING_DO_OCR` (scanned PDFs), `DATE_ORDER` (`MDY`/`DMY` for ambiguous
dates), `TEMPORAL_API_KEY` / `TEMPORAL_TLS_*` (Temporal Cloud).

To regenerate the sample PDFs/emails/golden files: `python scripts/generate_sample_data.py` (output is
byte-for-byte reproducible, so SHA-256 hashes stay stable).

## Troubleshooting

* **First Docling run is slow / fails offline** – Docling downloads its models from Hugging Face on first
  use. Pre-fetch with `docling-tools models download`, or set `DOCUMENT_PARSER=pdfplumber` temporarily.
* **Workflow stays "Running"** – the worker isn't running or listens on another task queue
  (`TEMPORAL_TASK_QUEUE`).
* **`--eml requires EMAIL_PROVIDER=local`** – the demo delivery helper only works with the local provider.
* **Resetting the demo** – `scripts/init_db.py --reset` clears PostgreSQL only. Temporal still remembers the
  completed workflow ids, so re-sending the same emails returns the earlier runs' results
  (`"duplicate_event": true`). For a clean slate also restart the dev server with a new `--db-filename`
  (or delete `temporal.db`), and `rm -rf var/`.
* **Reprocess a failed email** – fix the cause, then `python start_workflow.py --message-id <id> --wait`
  (failed workflow ids may be reused; the DB row is reused, not duplicated).
