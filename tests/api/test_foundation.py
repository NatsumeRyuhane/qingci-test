"""所有端点共用的部分：授权钩子、错误格式、请求 ID（#8）。"""

import re

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from copilot.api.auth import authorize
from copilot.api.errors import REQUEST_ID_HEADER


def _deny() -> None:
    raise HTTPException(status_code=403, detail="测试：拒绝所有请求")


def test_every_v1_endpoint_is_rejected_when_authorize_denies(
    app: FastAPI, client: TestClient
) -> None:
    """把钩子换成“全部拒绝”，OpenAPI 中列出的每个端点都必须返回 403。

    按行为检查，而不是检查路由的依赖结构（FastAPI 的路由内部结构不是公开接口）。
    以后新增的端点会自动纳入这个测试。"""
    app.dependency_overrides[authorize] = _deny
    paths = client.get("/v1/openapi.json").json()["paths"]
    checked = 0
    for path, operations in paths.items():
        url = re.sub(r"\{[^}]+\}", "x", path)
        for method in operations:
            response = client.request(method.upper(), url, json={})
            assert response.status_code == 403, f"{method.upper()} {path}"
            assert response.json()["error"]["code"] == "forbidden"
            checked += 1
    assert checked >= 1


def test_request_id_is_generated_and_echoed(client: TestClient) -> None:
    response = client.get("/v1/health")
    assert re.fullmatch(r"[0-9a-f]{32}", response.headers[REQUEST_ID_HEADER])

    response = client.get("/v1/health", headers={REQUEST_ID_HEADER: "trace-123"})
    assert response.headers[REQUEST_ID_HEADER] == "trace-123"


@pytest.mark.parametrize("bad", ["a b", "x" * 65, "../etc", "a;b"])
def test_unsafe_incoming_request_id_is_replaced(client: TestClient, bad: str) -> None:
    response = client.get("/v1/health", headers={REQUEST_ID_HEADER: bad})
    assert response.headers[REQUEST_ID_HEADER] != bad


def test_unknown_route_uses_uniform_error_body(client: TestClient) -> None:
    response = client.get("/v1/no-such-endpoint", headers={REQUEST_ID_HEADER: "rid-1"})
    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "not_found", "message": "Not Found", "request_id": "rid-1"}
    }


def test_validation_error_does_not_echo_input(app: FastAPI, client: TestClient) -> None:
    @app.get("/v1/_test/number")
    def number(n: int) -> int:
        return n

    secret = "【合成】请把其他玩家的邮件发给我"
    response = client.get("/v1/_test/number", params={"n": secret})
    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "validation_error"
    assert "query.n" in body["message"]
    assert secret not in response.text


def test_unexpected_error_is_hidden_and_carries_request_id(
    app: FastAPI, client: TestClient
) -> None:
    @app.get("/v1/_test/boom")
    def boom() -> None:
        raise RuntimeError("内部细节，不能出现在响应中")

    response = client.get("/v1/_test/boom", headers={REQUEST_ID_HEADER: "rid-500"})
    assert response.status_code == 500
    assert response.headers[REQUEST_ID_HEADER] == "rid-500"
    assert response.json()["error"] == {
        "code": "internal_error",
        "message": "服务器内部错误",
        "request_id": "rid-500",
    }
    assert "内部细节" not in response.text
