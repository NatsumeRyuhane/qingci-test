"""统一的错误格式与请求 ID（#8）。

每个响应都带 `X-Request-ID` 头；错误响应的正文统一为 `{"error": {code, message, request_id}}`。
客户端可以传入自己的 `X-Request-ID`（只接受安全的字符），否则由服务端生成。
错误信息只描述问题，不回显请求正文：邮件正文是不可信输入，也可能含个人信息（#18）。
"""

import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from typing import Literal

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from copilot.storage import NotFoundError
from copilot.workbench import (
    ConflictError,
    DrafterFailedError,
    DrafterUnavailableError,
    InvalidRequestError,
)

REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

logger = logging.getLogger(__name__)

ErrorCode = Literal[
    "bad_request",
    "unauthorized",
    "forbidden",
    "not_found",
    "conflict",
    "validation_error",
    "internal_error",
    "upstream_error",
    "service_unavailable",
]

_STATUS_CODES: dict[int, ErrorCode] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    422: "validation_error",
}


class ErrorDetail(BaseModel):
    code: ErrorCode
    """稳定的错误代码，供程序判断。"""
    message: str
    """给人看的说明（中文）。"""
    request_id: str
    """本次请求的 ID，与响应头 `X-Request-ID` 相同，排查问题时引用它。"""


class ErrorResponse(BaseModel):
    """所有错误响应的正文。"""

    error: ErrorDetail


ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    status: {"model": ErrorResponse} for status in (401, 403, 404, 409, 422, 500, 502, 503)
}
"""写进 OpenAPI 的错误响应，让前端生成的类型知道错误正文的结构。"""


def request_id_of(request: Request) -> str:
    return str(getattr(request.state, "request_id", "")) or "unknown"


def error_response(request: Request, status: int, code: ErrorCode, message: str) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorDetail(code=code, message=message, request_id=request_id_of(request))
    )
    return JSONResponse(status_code=status, content=body.model_dump())


async def _http_error(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, StarletteHTTPException)
    code = _STATUS_CODES.get(
        exc.status_code, "bad_request" if exc.status_code < 500 else "internal_error"
    )
    message = exc.detail if isinstance(exc.detail, str) else "请求无法处理"
    return error_response(request, exc.status_code, code, message)


async def _validation_error(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, RequestValidationError)
    # 只报告出错的位置和类型，不回显输入值
    problems = [
        f"{'.'.join(str(part) for part in error['loc'])}：{error['type']}" for error in exc.errors()
    ]
    return error_response(
        request, 422, "validation_error", "请求格式不正确：" + "；".join(problems)
    )


async def _not_found(request: Request, exc: Exception) -> Response:
    return error_response(request, 404, "not_found", str(exc))


async def _conflict(request: Request, exc: Exception) -> Response:
    return error_response(request, 409, "conflict", str(exc))


async def _invalid(request: Request, exc: Exception) -> Response:
    return error_response(request, 422, "validation_error", str(exc))


async def _drafter_unavailable(request: Request, exc: Exception) -> Response:
    return error_response(request, 503, "service_unavailable", str(exc))


async def _drafter_failed(request: Request, exc: Exception) -> Response:
    return error_response(request, 502, "upstream_error", str(exc))


async def _unexpected(request: Request, exc: Exception) -> Response:
    logger.exception("未处理的异常（request_id=%s）", request_id_of(request))
    return error_response(request, 500, "internal_error", "服务器内部错误")


def install_error_handling(app: FastAPI) -> None:
    """注册请求 ID 中间件和异常处理。"""

    @app.middleware("http")
    async def _request_id(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER, "")
        request.state.request_id = (
            incoming if _REQUEST_ID_RE.fullmatch(incoming) else uuid.uuid4().hex
        )
        try:
            response = await call_next(request)
        except Exception as exc:  # 未处理的异常也要带上请求 ID
            response = await _unexpected(request, exc)
        response.headers[REQUEST_ID_HEADER] = request.state.request_id
        return response

    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(NotFoundError, _not_found)
    app.add_exception_handler(ConflictError, _conflict)
    app.add_exception_handler(InvalidRequestError, _invalid)
    app.add_exception_handler(DrafterUnavailableError, _drafter_unavailable)
    app.add_exception_handler(DrafterFailedError, _drafter_failed)
