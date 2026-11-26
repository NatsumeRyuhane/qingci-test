"""REST API（`/v1`）。前端和外部集成都只通过这一层访问后端（D-010）。"""

from copilot.api.app import create_app

__all__ = ["create_app"]
