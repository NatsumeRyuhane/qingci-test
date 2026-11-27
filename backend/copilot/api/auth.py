"""授权钩子。所有 `/v1` 端点都经过它（#8）。

MVP 中直接放行：界面上的“当前客服”只是 `Feedback.actor` 的取值，不是身份（#13）。
#10（服务账号）和 #13（基于身份的访问控制）只需替换 `authorize` 的实现，不用逐个端点改造。
测试中可以用 `app.dependency_overrides[authorize]` 替换它。
"""

from dataclasses import dataclass

from fastapi import Request


@dataclass(frozen=True)
class Principal:
    """发起请求的一方。MVP 中总是匿名。"""

    subject: str = "anonymous"


def authorize(request: Request) -> Principal:
    """决定请求能否继续。不允许时抛出 `HTTPException(401 或 403)`。"""
    principal = Principal()
    request.state.principal = principal
    return principal
