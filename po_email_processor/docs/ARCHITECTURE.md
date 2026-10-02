# Architecture & Design

## 1. Architecture

```
                 ┌──────────────────────────────┐
  Gmail API  ──► │  EmailProvider abstraction   │ ◄── local .eml folder (demo)
                 │  (raw RFC 822 bytes)         │     later: MS Graph / shared mailbox
                 └──────────────┬───────────────┘
                                │ list_new_message_ids()
                 ┌──────────────▼───────────────┐
                 │ Listener (start_workflow.py) │  starts 1 workflow per message
                 │ workflow id = f(message id)  │  ← idempotency layer 1
                 └──────────────┬───────────────┘
                                │ Temporal
┌───────────────────────────────▼────────────────────────────────────────────┐
│ PurchaseOrderEmailWorkflow  (deterministic orchestration only, no I/O)     │
│                                                                            │
│  fetch_email → extract_email_metadata → register_email ──(already done?)─► DUPLICATE
│        │                                                                   │
│        └─ for each PDF attachment:                                         │
│             download_po_attachment → register_document ──(same SHA-256?)─► DUPLICATE
│             → parse_document_with_docling → store_parsed_document          │
│             → extract_purchase_order      → store_extraction_result        │
│             → validate_purchase_order     → store_validation_result        │
│             → save_purchase_order_to_postgres ──(same PO number?)───────► DUPLICATE
│  → update_processing_status → acknowledge_email                            │
└───────────────────────────────┬────────────────────────────────────────────┘
                                │ activities (worker thread pool)
     ┌──────────────────┬───────┴──────────┬────────────────────┐
     ▼                  ▼                  ▼                    ▼
 EmailProvider      FileStore         Docling parser +      PostgreSQL
 (Gmail/local)   (raw email, PDF,    PO extractor +         repository
                  Docling output)     validation            (SQLAlchemy)
```

Layering rules:

* **Workflow** (`app/workflows/`) – only calls activities, branches on their results, and records progress. No clock, randomness, network, files or DB.
* **Activities** (`app/activities/`) – thin adapters: call a domain component, log, and translate permanent errors into non-retryable `ApplicationError`s.
* **Domain components** – `app/email/` (providers + MIME parsing), `app/extraction/` (parser, extractor, normalisation, validation), `app/database/` (repository), `app/storage/` (file store). None of them import Temporal, so they are unit-testable and reusable.
* **Interfaces** – `EmailProvider`, `DocumentParser`, `DocumentExtractor`. New mail sources, parsers (or an LLM extractor) and document types plug in through factories (`app/email/factory.py`, `app/extraction/factory.py`) without touching the workflow.
* Activities exchange **references** (file paths, ids) rather than large payloads; the raw email, the PDF and Docling's full output are written to the file store (swap for S3/ADLS later).

## 2. Folder structure

```
po_email_processor/
├── app/
│   ├── activities/
│   │   ├── email_activities.py      fetch_email, extract_email_metadata, download_po_attachment, acknowledge_email
│   │   ├── document_activities.py   parse_document_with_docling, extract_purchase_order, validate_purchase_order
│   │   ├── database_activities.py   register_email, register_document, store_*, save_purchase_order_to_postgres, update_processing_status
│   │   └── errors.py                permanent (non-retryable) error types
│   ├── workflows/purchase_order_workflow.py
│   ├── extraction/
│   │   ├── base.py                  DocumentParser / DocumentExtractor interfaces
│   │   ├── docling_parser.py        Docling DocumentConverter → ParsedDocument (+ Markdown + JSON)
│   │   ├── pdfplumber_parser.py     fallback parser, same output shape
│   │   ├── po_extractor.py          rule-based PO extractor (labels + tables)
│   │   ├── po_field_config.py       label vocabulary (future: per-supplier config)
│   │   ├── normalization.py         dates, money, quantities, emails, whitespace
│   │   ├── validation.py            business rules (errors vs warnings)
│   │   └── factory.py               picks parser/extractor from config / document type
│   ├── models/
│   │   ├── schemas.py               Pydantic contracts (EmailMetadata, PurchaseOrder, ...)
│   │   └── database.py              SQLAlchemy ORM models
│   ├── database/{connection,repository}.py
│   ├── email/{base,mime_parser,local_provider,gmail_client,factory}.py
│   ├── storage/file_store.py
│   ├── evaluation/golden.py         field-level comparison vs golden JSON
│   ├── config.py · logging_config.py · temporal_client.py
├── migrations/                       Alembic (versions/0001_initial_schema.py)
├── sql/                              schema.sql (generated DDL), verify_queries.sql
├── sample_data/
│   ├── pdfs/        3 PO layouts (US 1-page, EU 2-page/28 lines, minimal)
│   ├── emails/      6 .eml scenarios (normal, multi-page, minimal, resend, corrupt PDF, no attachment)
│   ├── expected/    golden JSON per PDF
│   └── inbox/       local provider inbox (demo drops emails here)
├── scripts/          generate_sample_data, init_db, show_results, evaluate_golden
├── tests/            unit, PostgreSQL, Temporal workflow and end-to-end tests
├── worker.py · start_workflow.py · docker-compose.yml · requirements*.txt · .env.example
```

## 3. Database schema

```
inbound_emails                         documents                               purchase_orders
──────────────────────────             ───────────────────────────             ────────────────────────────
id UUID PK                       ┌──── id UUID PK                        ┌──── id UUID PK
provider            ┐ UNIQUE     │     email_id FK → inbound_emails      │     document_id FK → documents  UNIQUE
provider_message_id ┘ (idempot.) │     attachment_index                  │     email_id FK → inbound_emails
internet_message_id              │     filename, mime_type, size_bytes   │     po_number
thread_id                        │     file_hash SHA-256  UNIQUE         │     dedupe_key UNIQUE (PO no.|supplier|buyer)
sender_email, sender_name        │     storage_path                      │     supplier_name/email/address
recipient_email TEXT[]           │     document_type                     │     buyer_name/address
cc TEXT[]                        │     parser, page_count                │     order_date, requested_delivery_date
subject, email_body              │     raw_extracted_text (Docling MD)   │     currency, subtotal, tax, shipping, total_amount
received_at                      │     raw_parser_output_path (JSON)     │     payment_terms, shipping_terms
attachments JSONB                │     extraction_json JSONB             │     ship_to_address, bill_to_address
raw_storage_path                 │     validation_json JSONB             │     validation_warnings JSONB
processing_status                │     processing_status                 │     created_at, updated_at
error_message, error_details     │     error_message, error_details      │
workflow_id, workflow_run_id     │     created_at, updated_at            │     purchase_order_items
created_at, updated_at           │                                       │     ─────────────────────────────
        1 ───────────────< ──────┘               1 ───────────────< ─────┘     id UUID PK
                                                                         ┌──── purchase_order_id FK  (UNIQUE with sequence)
processing_events (audit log)                       1 ──────────< ───────┘     sequence (order in document)
─────────────────────────────                                                  line_number (as printed)
id BIGSERIAL, entity_type (EMAIL|DOCUMENT), entity_id, status,                 part_number, part_name
message, details JSONB, workflow_id, workflow_run_id, created_at               quantity, unit_of_measure
                                                                               unit_price, line_total
                                                                               requested_delivery_date, created_at
```

Money and quantities are `NUMERIC(18,4)`; dates are `DATE`; timestamps are `TIMESTAMPTZ`. The full DDL is in `sql/schema.sql` (generated from the Alembic migration, which is the source of truth).

## 4. Temporal workflow / activity boundaries

| Activity | Kind of I/O | Timeout | Retry policy | Permanent (non-retryable) errors |
|---|---|---|---|---|
| `fetch_email` | email API, file write | 2 min | 2s → ×2 → 1 min, 5 attempts | `EmailNotFoundError` |
| `extract_email_metadata` | file read (+ Gmail thread id) | 2 min | same | `MalformedEmailError` |
| `download_po_attachment` | file read/write, SHA-256 | 2 min | same | `AttachmentNotFoundError` |
| `register_email`, `register_document`, `store_*`, `save_purchase_order_to_postgres`, `update_processing_status` | PostgreSQL | 30 s | 1s → ×2 → 30s, 10 attempts | – (all idempotent) |
| `parse_document_with_docling` | Docling (CPU, model load) | 10 min | 5s → ×2 → 2 min, 3 attempts | `DocumentParseError` |
| `extract_purchase_order` | file read (+ future LLM call) | 2 min | 2s → ×2 → 30s, 3 attempts | `ExtractionError` |
| `validate_purchase_order` | none today (future: LLM judge) | 2 min | same | – (returns a result) |
| `acknowledge_email` | email API / file move | 2 min | email policy | – |

* Validation **errors** are permanent: the workflow stores them, marks the document `FAILED` and raises a non-retryable `PurchaseOrderValidationError` – never retried. Validation **warnings** (arithmetic mismatches, missing optional fields) are stored with the PO.
* A failed document does not stop other attachments in the same email. If any document failed, the email is marked `FAILED` with per-document error details and the workflow ends as *Failed* in the Temporal UI.
* The workflow exposes a `progress` query (current stage + per-document outcomes).

### Idempotency (defence in depth)

1. **Workflow id** `po-email-{provider}-{message id}-{hash}` with `ALLOW_DUPLICATE_FAILED_ONLY`: a duplicate event for an email that is running or completed is rejected by Temporal itself.
2. **`inbound_emails (provider, provider_message_id)`** unique: a re-delivered email whose earlier run completed returns the earlier result as `DUPLICATE`; one that previously failed is reprocessed in place (no second row).
3. **`documents.file_hash`** (SHA-256) unique: the same PDF in a different email is recognised and linked to the existing PO.
4. **`purchase_orders.document_id`** unique: retried saves are no-ops.
5. **`purchase_orders.dedupe_key`** (normalised PO number + supplier + buyer) unique: a different file carrying the same PO (e.g. a re-scan) is marked `DUPLICATE`.

All inserts use `INSERT … ON CONFLICT DO NOTHING` + re-read, so concurrent or retried activities are safe.

### Processing statuses

`RECEIVED → EMAIL_PARSED → DOCUMENT_DOWNLOADED → DOCUMENT_PARSED → EXTRACTED → VALIDATED → SAVED`, plus terminal `DUPLICATE`, `IGNORED` (no PDF) and `FAILED` (with `error_message` and `error_details` JSONB). Every transition is appended to `processing_events` with the Temporal workflow and run ids.

## 5. Assumptions

* Every PDF attached to a monitored mailbox is treated as a Purchase Order. Non-PDF attachments are ignored; an email with no PDF is marked `IGNORED`. A document classifier activity can be inserted later (the `document_type` column and `DocumentType` enum already exist).
* PDFs are digital (text layer present). OCR is off by default for speed; set `DOCLING_DO_OCR=true` for scanned POs.
* PO number and at least one line item (with a part number or description, and a positive quantity) are required; everything else is optional and stays `NULL` when not present in the document. Arithmetic inconsistencies are warnings, not errors.
* Currency is only set when an ISO code or an unambiguous symbol (€, £, ₹) appears. A bare `$` is not assumed to be USD.
* Ambiguous numeric dates (`03/04/2026`) use `DATE_ORDER` (default `MDY`); unambiguous ones (`15/03/2026`) are always read correctly; dotted dates are day-first.
* When there is no explicit buyer label, the buyer is taken from the letterhead (the company name preceding the first labelled field), and a warning records that.
* If line numbers are not printed, they are assigned in row order (and a warning records that); the printed order is always kept in `purchase_order_items.sequence`.
* The same PO number from the same supplier and buyer is the same business PO. PO revisions/change orders are out of scope for v1 (they would be flagged as duplicates).
* A single worker process hosts all activities; the local file store is shared by the listener and worker (both run on the same machine in the demo). In production use object storage and split task queues (e.g. a dedicated Docling queue on bigger machines).
* Failed messages are still acknowledged (moved out of the inbox / labelled) so pollers don't loop on them; they remain `FAILED` in PostgreSQL for review and can be replayed with `start_workflow.py --message-id … --allow-rerun`.

## Future extensibility (designed for, not implemented)

| Capability | Where it plugs in |
|---|---|
| Supplier Quotes / RFQs / ASNs | new `DocumentExtractor` + `DocumentType`, registered in `extraction/factory.py`; new tables via Alembic |
| Document classifier | new activity before `extract_purchase_order`; result chooses the extractor |
| Email-body extraction | `EmailMetadata.email_body` is already stored; add an extractor over a `ParsedDocument` built from the body |
| Supplier-specific extraction | `po_field_config.py` is pure data – load per supplier from YAML/DB |
| LLM extraction / LLM-as-judge | implement `DocumentExtractor` (input: Docling Markdown + tables); judge inside `validate_purchase_order` |
| Field confidence & evidence | `ExtractionResult.field_confidence` / `field_evidence` (evidence already populated with the source text) |
| Human review | route documents with low confidence / warnings to a review status; a Temporal signal can resume the workflow |
| Golden dataset evaluation | `app/evaluation/golden.py` + `scripts/evaluate_golden.py` + `sample_data/expected/` |
| Azure / shared mailbox | implement `EmailProvider` over Microsoft Graph (`/messages/{id}/$value` returns RFC 822) |
| Databricks / ERP write-back | new activities after `save_purchase_order_to_postgres` (or a child workflow) |
