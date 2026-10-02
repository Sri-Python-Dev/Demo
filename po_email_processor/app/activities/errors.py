"""Mapping of domain exceptions to Temporal retry semantics.

Permanent problems (bad input) are raised as non-retryable ``ApplicationError``
so Temporal fails fast instead of retrying. Anything else (network blips,
database restarts, rate limits) propagates as a normal exception and is retried
according to the activity's ``RetryPolicy``.
"""

from __future__ import annotations

from temporalio.exceptions import ApplicationError

# Error type names; also listed in RetryPolicy.non_retryable_error_types.
EMAIL_NOT_FOUND = "EmailNotFoundError"
MALFORMED_EMAIL = "MalformedEmailError"
ATTACHMENT_NOT_FOUND = "AttachmentNotFoundError"
DOCUMENT_PARSE_ERROR = "DocumentParseError"
EXTRACTION_ERROR = "ExtractionError"
VALIDATION_ERROR = "PurchaseOrderValidationError"

NON_RETRYABLE_ERROR_TYPES = [
    EMAIL_NOT_FOUND,
    MALFORMED_EMAIL,
    ATTACHMENT_NOT_FOUND,
    DOCUMENT_PARSE_ERROR,
    EXTRACTION_ERROR,
    VALIDATION_ERROR,
]


def permanent(error_type: str, exc: BaseException | str, **details) -> ApplicationError:
    message = str(exc)
    return ApplicationError(message, details or None, type=error_type, non_retryable=True)
