"""端点共用的依赖：数据库、仓库与起草服务。"""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import Engine

from copilot.storage import Repository, SqlRepository, create_engine_for, session_scope
from copilot.workbench import Drafter


def engine_of(request: Request) -> Engine:
    """应用共享的数据库引擎，第一次用到时创建。

    延迟创建：`copilot openapi` 等只需要应用定义的场合不会去碰数据库文件。
    """
    state = request.app.state
    if state.engine is None:
        state.engine = create_engine_for(state.database_url)
    engine: Engine = state.engine
    return engine


def repository(engine: Annotated[Engine, Depends(engine_of)]) -> Iterator[Repository]:
    """一个请求一个事务：端点正常返回时提交，抛出异常时回滚。"""
    with session_scope(engine) as session:
        yield SqlRepository(session)


Repo = Annotated[Repository, Depends(repository)]


def drafter_of(request: Request) -> Drafter:
    """应用使用的起草服务（`create_app(drafter=...)`）。"""
    drafter: Drafter = request.app.state.drafter
    return drafter


DrafterDep = Annotated[Drafter, Depends(drafter_of)]
