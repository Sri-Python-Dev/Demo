-- PostgreSQL schema for the PO email processor.
-- Generated from the Alembic migrations: alembic upgrade head --sql
-- Prefer 'python scripts/init_db.py' (Alembic); this file is for review / DBAs.

BEGIN;

CREATE TABLE alembic_version (
    version_num VARCHAR(32) NOT NULL, 
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);

-- Running upgrade  -> 0001

CREATE TABLE inbound_emails (
    id UUID NOT NULL, 
    provider VARCHAR(32) NOT NULL, 
    provider_message_id VARCHAR(512) NOT NULL, 
    internet_message_id VARCHAR(998), 
    thread_id VARCHAR(998), 
    sender_email VARCHAR(320), 
    sender_name VARCHAR(320), 
    recipient_email TEXT[] DEFAULT '{}' NOT NULL, 
    cc TEXT[] DEFAULT '{}' NOT NULL, 
    subject TEXT, 
    email_body TEXT, 
    received_at TIMESTAMP WITH TIME ZONE, 
    attachments JSONB DEFAULT '[]' NOT NULL, 
    raw_storage_path TEXT, 
    processing_status VARCHAR(32) DEFAULT 'RECEIVED' NOT NULL, 
    error_message TEXT, 
    error_details JSONB, 
    workflow_id VARCHAR(512), 
    workflow_run_id VARCHAR(128), 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_inbound_emails_provider_message UNIQUE (provider, provider_message_id)
);

CREATE INDEX ix_inbound_emails_received_at ON inbound_emails (received_at);

CREATE INDEX ix_inbound_emails_status ON inbound_emails (processing_status);

CREATE TABLE processing_events (
    id BIGSERIAL NOT NULL, 
    entity_type VARCHAR(16) NOT NULL, 
    entity_id UUID NOT NULL, 
    status VARCHAR(32) NOT NULL, 
    message TEXT, 
    details JSONB, 
    workflow_id VARCHAR(512), 
    workflow_run_id VARCHAR(128), 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_processing_events_entity ON processing_events (entity_type, entity_id);

CREATE TABLE documents (
    id UUID NOT NULL, 
    email_id UUID NOT NULL, 
    attachment_index INTEGER, 
    filename TEXT NOT NULL, 
    mime_type VARCHAR(255) NOT NULL, 
    size_bytes BIGINT, 
    file_hash VARCHAR(64) NOT NULL, 
    storage_path TEXT, 
    document_type VARCHAR(32) DEFAULT 'PURCHASE_ORDER' NOT NULL, 
    parser VARCHAR(64), 
    page_count INTEGER, 
    raw_extracted_text TEXT, 
    raw_parser_output_path TEXT, 
    extraction_json JSONB, 
    validation_json JSONB, 
    processing_status VARCHAR(32) DEFAULT 'DOCUMENT_DOWNLOADED' NOT NULL, 
    error_message TEXT, 
    error_details JSONB, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(email_id) REFERENCES inbound_emails (id) ON DELETE CASCADE, 
    CONSTRAINT uq_documents_file_hash UNIQUE (file_hash)
);

CREATE INDEX ix_documents_email_id ON documents (email_id);

CREATE INDEX ix_documents_status ON documents (processing_status);

CREATE TABLE purchase_orders (
    id UUID NOT NULL, 
    document_id UUID NOT NULL, 
    email_id UUID NOT NULL, 
    po_number VARCHAR(128) NOT NULL, 
    dedupe_key VARCHAR(512) NOT NULL, 
    supplier_name TEXT, 
    supplier_email VARCHAR(320), 
    supplier_address TEXT, 
    buyer_name TEXT, 
    buyer_address TEXT, 
    order_date DATE, 
    requested_delivery_date DATE, 
    currency VARCHAR(3), 
    subtotal NUMERIC(18, 4), 
    tax NUMERIC(18, 4), 
    shipping NUMERIC(18, 4), 
    total_amount NUMERIC(18, 4), 
    payment_terms TEXT, 
    shipping_terms TEXT, 
    ship_to_address TEXT, 
    bill_to_address TEXT, 
    validation_warnings JSONB DEFAULT '[]' NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(document_id) REFERENCES documents (id) ON DELETE CASCADE, 
    FOREIGN KEY(email_id) REFERENCES inbound_emails (id) ON DELETE CASCADE, 
    CONSTRAINT uq_purchase_orders_dedupe_key UNIQUE (dedupe_key), 
    CONSTRAINT uq_purchase_orders_document UNIQUE (document_id)
);

CREATE INDEX ix_purchase_orders_po_number ON purchase_orders (po_number);

CREATE INDEX ix_purchase_orders_supplier ON purchase_orders (supplier_name);

CREATE TABLE purchase_order_items (
    id UUID NOT NULL, 
    purchase_order_id UUID NOT NULL, 
    sequence INTEGER NOT NULL, 
    line_number INTEGER, 
    part_number VARCHAR(255), 
    part_name TEXT, 
    quantity NUMERIC(18, 4), 
    unit_of_measure VARCHAR(32), 
    unit_price NUMERIC(18, 4), 
    line_total NUMERIC(18, 4), 
    requested_delivery_date DATE, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(purchase_order_id) REFERENCES purchase_orders (id) ON DELETE CASCADE, 
    CONSTRAINT uq_purchase_order_items_sequence UNIQUE (purchase_order_id, sequence)
);

CREATE INDEX ix_purchase_order_items_part_number ON purchase_order_items (part_number);

INSERT INTO alembic_version (version_num) VALUES ('0001') RETURNING alembic_version.version_num;

COMMIT;

