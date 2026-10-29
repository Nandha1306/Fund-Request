"""Error contract (architecture doc, section 8.2).

400 malformed input · 401 not authenticated · 403 visible but not allowed ·
404 not visible or missing · 409 wrong state / duplicate · 422 business rule.
"""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class AppError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, *, details: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class InvalidFile(AppError):
    """An upload that is empty, too large or not an accepted type (checked by content)."""

    status_code = 400
    code = "invalid_file"


class NotAuthenticated(AppError):
    status_code = 401
    code = "not_authenticated"


class Forbidden(AppError):
    status_code = 403
    code = "forbidden"


class NotFound(AppError):
    status_code = 404
    code = "not_found"


class Conflict(AppError):
    status_code = 409
    code = "conflict"


class BusinessRuleViolation(AppError):
    status_code = 422
    code = "business_rule"


def _body(code: str, message: str, details: Any = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details}}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=_body(exc.code, exc.message, exc.details))

    # FastAPI answers schema errors with 422 by default; remap to 400 so 422 only ever means "business rule".
    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content=_body("validation_error", "Request is invalid", jsonable_encoder(exc.errors())),
        )
