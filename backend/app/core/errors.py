"""
Consistent error handling.

Every error the API returns (validation, auth, not-found, server error) uses
the same JSON envelope:

    {"success": false, "error": {"code": "...", "message": "..."}}

so the ESP32 firmware and the frontend only need ONE parsing path for errors.
"""
from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class ApiError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        self.code = code
        self.message = message
        self.status_code = status_code


def envelope(code: str, message: str) -> dict:
    return {"success": False, "error": {"code": code, "message": message}}


async def api_error_handler(request: Request, exc: ApiError):
    return JSONResponse(status_code=exc.status_code, content=envelope(exc.code, exc.message))


async def validation_error_handler(request: Request, exc: RequestValidationError):
    first = exc.errors()[0] if exc.errors() else {}
    field = ".".join(str(x) for x in first.get("loc", [])[1:]) or "body"
    message = f"{field}: {first.get('msg', 'invalid request')}"
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=envelope("VALIDATION_ERROR", message),
    )


async def unhandled_error_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=envelope("INTERNAL_ERROR", "An unexpected error occurred."),
    )
