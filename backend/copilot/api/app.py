"""FastAPI 应用的组装。"""

from fastapi import APIRouter, FastAPI
from pydantic import BaseModel

from copilot import __version__

API_PREFIX = "/v1"


class Health(BaseModel):
    """`GET /v1/health` 的响应。"""

    status: str
    version: str


def _v1_router() -> APIRouter:
    router = APIRouter(prefix=API_PREFIX)

    @router.get("/health", tags=["meta"])
    def health() -> Health:
        """服务存活检查，供前端、部署脚本和容器健康检查使用。"""
        return Health(status="ok", version=__version__)

    return router


def create_app() -> FastAPI:
    """创建应用。OpenAPI 文档放在 `/v1` 下，与 API 版本一起演进。"""
    app = FastAPI(
        title="qingci copilot",
        version=__version__,
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url=f"{API_PREFIX}/docs",
        redoc_url=None,
    )
    app.include_router(_v1_router())
    return app
