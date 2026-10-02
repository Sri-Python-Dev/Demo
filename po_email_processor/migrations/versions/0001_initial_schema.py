"""initial schema

Revision ID: 0001
Revises: 
Create Date: 2026-10-02 13:55:07.286551
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('inbound_emails',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('provider', sa.String(length=32), nullable=False),
    sa.Column('provider_message_id', sa.String(length=512), nullable=False),
    sa.Column('internet_message_id', sa.String(length=998), nullable=True),
    sa.Column('thread_id', sa.String(length=998), nullable=True),
    sa.Column('sender_email', sa.String(length=320), nullable=True),
    sa.Column('sender_name', sa.String(length=320), nullable=True),
    sa.Column('recipient_email', postgresql.ARRAY(sa.Text()), server_default='{}', nullable=False),
    sa.Column('cc', postgresql.ARRAY(sa.Text()), server_default='{}', nullable=False),
    sa.Column('subject', sa.Text(), nullable=True),
    sa.Column('email_body', sa.Text(), nullable=True),
    sa.Column('received_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('attachments', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('raw_storage_path', sa.Text(), nullable=True),
    sa.Column('processing_status', sa.String(length=32), server_default='RECEIVED', nullable=False),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('error_details', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('workflow_id', sa.String(length=512), nullable=True),
    sa.Column('workflow_run_id', sa.String(length=128), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('provider', 'provider_message_id', name='uq_inbound_emails_provider_message')
    )
    op.create_index('ix_inbound_emails_received_at', 'inbound_emails', ['received_at'], unique=False)
    op.create_index('ix_inbound_emails_status', 'inbound_emails', ['processing_status'], unique=False)
    op.create_table('processing_events',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('entity_type', sa.String(length=16), nullable=False),
    sa.Column('entity_id', sa.UUID(), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('message', sa.Text(), nullable=True),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('workflow_id', sa.String(length=512), nullable=True),
    sa.Column('workflow_run_id', sa.String(length=128), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_processing_events_entity', 'processing_events', ['entity_type', 'entity_id'], unique=False)
    op.create_table('documents',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('email_id', sa.UUID(), nullable=False),
    sa.Column('attachment_index', sa.Integer(), nullable=True),
    sa.Column('filename', sa.Text(), nullable=False),
    sa.Column('mime_type', sa.String(length=255), nullable=False),
    sa.Column('size_bytes', sa.BigInteger(), nullable=True),
    sa.Column('file_hash', sa.String(length=64), nullable=False),
    sa.Column('storage_path', sa.Text(), nullable=True),
    sa.Column('document_type', sa.String(length=32), server_default='PURCHASE_ORDER', nullable=False),
    sa.Column('parser', sa.String(length=64), nullable=True),
    sa.Column('page_count', sa.Integer(), nullable=True),
    sa.Column('raw_extracted_text', sa.Text(), nullable=True),
    sa.Column('raw_parser_output_path', sa.Text(), nullable=True),
    sa.Column('extraction_json', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('validation_json', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('processing_status', sa.String(length=32), server_default='DOCUMENT_DOWNLOADED', nullable=False),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('error_details', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['email_id'], ['inbound_emails.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('file_hash', name='uq_documents_file_hash')
    )
    op.create_index('ix_documents_email_id', 'documents', ['email_id'], unique=False)
    op.create_index('ix_documents_status', 'documents', ['processing_status'], unique=False)
    op.create_table('purchase_orders',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('document_id', sa.UUID(), nullable=False),
    sa.Column('email_id', sa.UUID(), nullable=False),
    sa.Column('po_number', sa.String(length=128), nullable=False),
    sa.Column('dedupe_key', sa.String(length=512), nullable=False),
    sa.Column('supplier_name', sa.Text(), nullable=True),
    sa.Column('supplier_email', sa.String(length=320), nullable=True),
    sa.Column('supplier_address', sa.Text(), nullable=True),
    sa.Column('buyer_name', sa.Text(), nullable=True),
    sa.Column('buyer_address', sa.Text(), nullable=True),
    sa.Column('order_date', sa.Date(), nullable=True),
    sa.Column('requested_delivery_date', sa.Date(), nullable=True),
    sa.Column('currency', sa.String(length=3), nullable=True),
    sa.Column('subtotal', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('tax', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('shipping', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('total_amount', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('payment_terms', sa.Text(), nullable=True),
    sa.Column('shipping_terms', sa.Text(), nullable=True),
    sa.Column('ship_to_address', sa.Text(), nullable=True),
    sa.Column('bill_to_address', sa.Text(), nullable=True),
    sa.Column('validation_warnings', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['email_id'], ['inbound_emails.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('dedupe_key', name='uq_purchase_orders_dedupe_key'),
    sa.UniqueConstraint('document_id', name='uq_purchase_orders_document')
    )
    op.create_index('ix_purchase_orders_po_number', 'purchase_orders', ['po_number'], unique=False)
    op.create_index('ix_purchase_orders_supplier', 'purchase_orders', ['supplier_name'], unique=False)
    op.create_table('purchase_order_items',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('purchase_order_id', sa.UUID(), nullable=False),
    sa.Column('sequence', sa.Integer(), nullable=False),
    sa.Column('line_number', sa.Integer(), nullable=True),
    sa.Column('part_number', sa.String(length=255), nullable=True),
    sa.Column('part_name', sa.Text(), nullable=True),
    sa.Column('quantity', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('unit_of_measure', sa.String(length=32), nullable=True),
    sa.Column('unit_price', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('line_total', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('requested_delivery_date', sa.Date(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['purchase_order_id'], ['purchase_orders.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('purchase_order_id', 'sequence', name='uq_purchase_order_items_sequence')
    )
    op.create_index('ix_purchase_order_items_part_number', 'purchase_order_items', ['part_number'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_purchase_order_items_part_number', table_name='purchase_order_items')
    op.drop_table('purchase_order_items')
    op.drop_index('ix_purchase_orders_supplier', table_name='purchase_orders')
    op.drop_index('ix_purchase_orders_po_number', table_name='purchase_orders')
    op.drop_table('purchase_orders')
    op.drop_index('ix_documents_status', table_name='documents')
    op.drop_index('ix_documents_email_id', table_name='documents')
    op.drop_table('documents')
    op.drop_index('ix_processing_events_entity', table_name='processing_events')
    op.drop_table('processing_events')
    op.drop_index('ix_inbound_emails_status', table_name='inbound_emails')
    op.drop_index('ix_inbound_emails_received_at', table_name='inbound_emails')
    op.drop_table('inbound_emails')
