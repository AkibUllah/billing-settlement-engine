"""Exception type the API views use to report business-rule violations."""

from __future__ import annotations

from rest_framework import status
from rest_framework.exceptions import APIException


class DomainError(APIException):
    """A service-layer error (invalid transition, bad fee schedule, unparsable report).

    Raised from views instead of building a `Response` from the caught exception, so DRF's
    exception handler renders the usual `{"detail": "..."}` body with the given status code.
    """

    status_code = status.HTTP_400_BAD_REQUEST
    default_code = "domain_error"

    def __init__(self, detail: str, status_code: int | None = None) -> None:
        super().__init__(detail)
        if status_code is not None:
            self.status_code = status_code
