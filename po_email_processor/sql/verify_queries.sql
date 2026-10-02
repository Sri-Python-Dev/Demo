-- Demo verification queries.
-- Run with:  psql "$PSQL_URL" -f sql/verify_queries.sql
-- (PSQL_URL is DATABASE_URL without the "+psycopg" driver suffix.)

\echo '=== 1. Inbound emails and their processing status'
SELECT provider_message_id,
       sender_name,
       sender_email,
       recipient_email,
       cc,
       subject,
       received_at,
       processing_status,
       left(error_message, 80) AS error
FROM inbound_emails
ORDER BY received_at;

\echo '=== 2. Documents (attachments) with hash, parser and status'
SELECT d.filename,
       d.mime_type,
       d.size_bytes,
       left(d.file_hash, 16) || '...' AS sha256,
       d.parser,
       d.page_count,
       d.processing_status,
       left(d.error_message, 80) AS error,
       e.subject AS from_email
FROM documents d
JOIN inbound_emails e ON e.id = d.email_id
ORDER BY d.created_at;

\echo '=== 3. Purchase order headers'
SELECT po_number, supplier_name, supplier_email, buyer_name, order_date, requested_delivery_date,
       currency, subtotal, tax, shipping, total_amount, payment_terms, shipping_terms
FROM purchase_orders
ORDER BY order_date;

\echo '=== 4. Line items for PO-100245'
SELECT i.line_number, i.part_number, i.part_name, i.quantity, i.unit_of_measure, i.unit_price, i.line_total
FROM purchase_order_items i
JOIN purchase_orders p ON p.id = i.purchase_order_id
WHERE p.po_number = 'PO-100245'
ORDER BY i.sequence;

\echo '=== 5. Line-item count and reconciliation per PO (sum of lines vs subtotal)'
SELECT p.po_number,
       count(i.id)          AS line_items,
       sum(i.line_total)    AS sum_of_lines,
       p.subtotal,
       p.total_amount,
       p.currency
FROM purchase_orders p
JOIN purchase_order_items i ON i.purchase_order_id = p.id
GROUP BY p.id
ORDER BY p.po_number;

\echo '=== 6. Full lineage: email -> document -> PO (one row per PO)'
SELECT e.provider_message_id, e.sender_email, e.subject, d.filename, p.po_number, p.total_amount, e.workflow_id
FROM purchase_orders p
JOIN documents d ON d.id = p.document_id
JOIN inbound_emails e ON e.id = p.email_id;

\echo '=== 7. Idempotency check: no duplicate POs / documents / emails (all should return 0 rows)'
SELECT po_number, count(*) FROM purchase_orders GROUP BY po_number, supplier_name HAVING count(*) > 1;
SELECT file_hash, count(*) FROM documents GROUP BY file_hash HAVING count(*) > 1;
SELECT provider_message_id, count(*) FROM inbound_emails GROUP BY provider, provider_message_id HAVING count(*) > 1;

\echo '=== 8. Audit trail for the most recent email'
SELECT ev.created_at, ev.entity_type, ev.status, left(ev.message, 90) AS message
FROM processing_events ev
WHERE ev.entity_id IN (
    SELECT id FROM inbound_emails WHERE id = (SELECT id FROM inbound_emails ORDER BY created_at DESC LIMIT 1)
    UNION
    SELECT d.id FROM documents d WHERE d.email_id = (SELECT id FROM inbound_emails ORDER BY created_at DESC LIMIT 1)
)
ORDER BY ev.id;

\echo '=== 9. Failures needing attention'
SELECT 'email' AS entity, provider_message_id AS ref, error_message, error_details
FROM inbound_emails WHERE processing_status = 'FAILED'
UNION ALL
SELECT 'document', filename, error_message, error_details
FROM documents WHERE processing_status = 'FAILED';

\echo '=== 10. Raw structured extraction JSON (JSONB) for PO-100245'
SELECT jsonb_pretty(d.extraction_json -> 'purchase_order') AS extraction
FROM documents d
JOIN purchase_orders p ON p.document_id = d.id
WHERE p.po_number = 'PO-100245';
