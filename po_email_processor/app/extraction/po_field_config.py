"""Label vocabulary for Purchase Order extraction.

Different suppliers/buyers label the same field differently ("PO #",
"Order No.", "Purchase Order Number" ...). This module is pure configuration
so it can later be extended per supplier (e.g. loaded from YAML / a DB table)
without touching extractor or workflow code.

Labels are regular expressions matched case-insensitively at the start of a
text line or as the whole content of a table cell. When several labels match,
the longest match wins, so "Vendor Email" beats "Vendor" and "Total Amount"
beats "Total".
"""

from __future__ import annotations

# field name -> list of label regexes
HEADER_FIELD_LABELS: dict[str, list[str]] = {
    "purchase_order_number": [
        r"purchase\s+order\s+(?:number|no\.?|num\.?|#)",
        r"p\.?\s?o\.?\s+(?:number|no\.?|num\.?|#)",
        r"p\.?\s?o\.?\s*#",
        r"order\s+(?:number|no\.?|num\.?|#)",
        r"purchase\s+order",
        r"po",
    ],
    "order_date": [
        r"(?:purchase\s+)?order\s+date",
        r"p\.?\s?o\.?\s+date",
        r"date\s+of\s+order",
        r"issue\s+date",
        r"date\s+issued",
        r"date",
    ],
    "requested_delivery_date": [
        r"requested\s+delivery\s+date",
        r"required\s+delivery\s+date",
        r"requested\s+ship\s+date",
        r"delivery\s+date",
        r"need[\s-]+by(?:\s+date)?",
        r"required\s+(?:by|date)",
        r"deliver\s+by",
        r"due\s+date",
    ],
    "currency": [r"currency(?:\s+code)?"],
    "supplier_name": [
        r"supplier(?:\s+name)?",
        r"vendor(?:\s+name)?",
        r"sold\s+by",
        r"seller",
    ],
    "supplier_email": [
        r"(?:supplier|vendor)\s+e-?mail(?:\s+address)?",
    ],
    "supplier_address": [r"(?:supplier|vendor)\s+address"],
    "buyer_name": [
        r"buyer(?:\s+name)?",
        r"customer(?:\s+name)?",
        r"purchaser",
        r"ordered\s+by",
        r"issued\s+by",
    ],
    "buyer_address": [r"(?:buyer|customer|purchaser)\s+address"],
    "ship_to_address": [
        r"ship[\s-]+to(?:\s+address)?",
        r"deliver[\s-]+to",
        r"delivery\s+address",
        r"shipping\s+address",
        r"consignee",
    ],
    "bill_to_address": [
        r"bill[\s-]+to(?:\s+address)?",
        r"invoice[\s-]+to",
        r"billing\s+address",
        r"invoice\s+address",
    ],
    "payment_terms": [
        r"payment\s+terms",
        r"terms\s+of\s+payment",
        r"payment\s+conditions",
        r"terms",
    ],
    "shipping_terms": [
        r"inco\s?terms?",
        r"shipping\s+terms",
        r"delivery\s+terms",
        r"freight\s+terms",
        r"terms\s+of\s+delivery",
    ],
    "subtotal": [
        r"sub[\s-]?total",
        r"net\s+(?:amount|total|value)",
        r"total\s+net",
        r"merchandise\s+total",
        r"goods\s+total",
    ],
    "tax": [
        r"(?:sales\s+)?tax(?:\s+amount)?",
        r"total\s+tax",
        r"vat",
        r"gst",
        r"hst",
    ],
    "shipping": [
        r"shipping\s+(?:&|and)\s+handling",
        r"shipping(?:\s+(?:charges?|cost|fee))?",
        r"freight(?:\s+(?:charges?|cost))?",
        r"delivery\s+charges?",
        r"carriage",
        r"s\s?&\s?h",
    ],
    "total_amount": [
        r"grand\s+total",
        r"total\s+amount(?:\s+due)?",
        r"order\s+total",
        r"po\s+total",
        r"total\s+(?:due|value|price)",
        r"amount\s+due",
        r"gross\s+(?:amount|total)",
        r"total",
    ],
}

# Fields whose value is a block of text (name and/or address).
PARTY_FIELDS = {"supplier_name", "buyer_name"}
ADDRESS_FIELDS = {"supplier_address", "buyer_address", "ship_to_address", "bill_to_address"}
MONEY_FIELDS = {"subtotal", "tax", "shipping", "total_amount"}
DATE_FIELDS = {"order_date", "requested_delivery_date"}

# Line-item table column vocabulary. Matched against the whole header cell.
LINE_ITEM_COLUMNS: dict[str, list[str]] = {
    "line_number": [r"line(?:\s+(?:no\.?|number|#))?", r"ln", r"pos\.?", r"position", r"#", r"no\.?", r"s\.?\s?no\.?", r"sr\.?\s?no\.?"],
    "part_number": [
        r"part\s*(?:number|no\.?|num\.?|#)", r"part", r"item\s*(?:number|no\.?|code|#|id)", r"sku",
        r"material(?:\s+(?:number|no\.?|code))?", r"product\s*(?:code|number|no\.?|id)", r"article(?:\s+(?:no\.?|number))?",
        r"catalog(?:ue)?\s*(?:number|no\.?|#)", r"mfr\.?\s*part(?:\s*(?:no\.?|number|#))?",
    ],
    "part_name": [
        r"(?:item\s+|part\s+|product\s+)?description", r"part\s+name", r"item\s+name", r"product(?:\s+name)?",
        r"material\s+description", r"details",
    ],
    "quantity": [r"qty\.?(?:\s+ordered)?", r"quantity(?:\s+ordered)?", r"order(?:ed)?\s+qty\.?", r"units"],
    "unit_of_measure": [r"uom", r"u/m", r"unit\s+of\s+measure", r"unit", r"um"],
    "unit_price": [r"unit\s+(?:price|cost)", r"price(?:\s+per\s+unit)?", r"rate", r"unit\s+rate", r"cost"],
    "line_total": [
        r"line\s+total", r"(?:net\s+|extended\s+|ext\.?\s+|line\s+)?amount", r"ext(?:ended|\.)?\s+price",
        r"total(?:\s+price)?", r"net\s+value", r"value",
    ],
    "requested_delivery_date": [
        r"(?:requested\s+)?delivery\s+date", r"need[\s-]+by(?:\s+date)?", r"due\s+date", r"required\s+date",
        r"(?:requested\s+)?ship\s+date", r"delivery",
    ],
}

# An ambiguous "Item" header is resolved from the column's values.
AMBIGUOUS_ITEM_HEADER = r"item"

# Legal-entity suffixes used to split "Company Ltd., 1 Main St" into name/address.
COMPANY_SUFFIX = (
    r"(?:inc|incorporated|ltd|limited|llc|l\.l\.c|corp|corporation|co|company|gmbh|ag|plc|"
    r"s\.?r\.?l|s\.?a|b\.?v|n\.?v|pvt\.?\s+ltd|pty\.?\s+ltd|kg|oy|ab|as|sas|spa|s\.p\.a)\.?"
)
