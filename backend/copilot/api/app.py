"""FastAPI 应用的组装。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel

from copilot import __version__
from copilot.api.auth import authorize
from copilot.api.errors import ERROR_RESPONSES, install_error_handling
from copilot.config import Settings, get_settings

API_PREFIX = "/v1"


class Health(BaseModel):
    """`GET /v1/health` 的响应。"""

    status: str
    version: str


def _meta_router() -> APIRouter:
    router = APIRouter(tags=["meta"])

    @router.get("/health")
    def health() -> Health:
        """服务存活检查，供前端、部署脚本和容器健康检查使用。"""
        return Health(status="ok", version=__version__)

    return router


def _v1_router() -> APIRouter:
    """所有 `/v1` 端点。`authorize` 挂在这一层，任何端点都不能绕过它（#8）。"""
    router = APIRouter(
        prefix=API_PREFIX, dependencies=[Depends(authorize)], responses=ERROR_RESPONSES
    )
    router.include_router(_meta_router())
    return router


def create_app(settings: Settings | None = None) -> FastAPI:
    """创建应用。OpenAPI 文档放在 `/v1` 下，与 API 版本一起演进。

    数据库引擎在第一次用到时才创建（见 `copilot.api.deps`），只生成 OpenAPI 时不碰数据库。
    """
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        if app.state.engine is not None:
            app.state.engine.dispose()

    app = FastAPI(
        title="qingci copilot",
        version=__version__,
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url=f"{API_PREFIX}/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.database_url = settings.database_url
    app.state.engine = None
    install_error_handling(app)
    app.include_router(_v1_router())
    return app
