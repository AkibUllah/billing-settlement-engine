"""Maps service-layer exceptions to HTTP responses for every DRF view.

Views call the services directly and let domain errors propagate; this handler (configured as
REST_FRAMEWORK["EXCEPTION_HANDLER"]) turns them into the usual `{"detail": "..."}` body.
State conflicts become 409, every other business-rule violation 400.
"""

from __future__ import annotations

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler

from apps.billing.services import BillingError, InvoiceStateError
from apps.ledger.services import LedgerError
from apps.settlement.services import InvalidTransition, SettlementError

CONFLICT_ERRORS = (InvalidTransition, InvoiceStateError)
BAD_REQUEST_ERRORS = (BillingError, SettlementError, LedgerError)


def domain_exception_handler(exc, context):
    if isinstance(exc, CONFLICT_ERRORS):
        return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
    if isinstance(exc, BAD_REQUEST_ERRORS):
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return exception_handler(exc, context)
