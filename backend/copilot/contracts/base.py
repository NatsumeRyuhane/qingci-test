"""所有契约模型共用的基类。"""

from pydantic import BaseModel, ConfigDict


class Contract(BaseModel):
    """契约模型的基类。

    - `extra="forbid"`：拼错或多余的字段直接报错，而不是被悄悄丢掉。
    - `use_attribute_docstrings=True`：字段下方的 docstring 会成为 JSON Schema 中的
      `description`，所以字段说明只写一处，对内（代码）和对外（#10、#11 发布的 schema）一致。
    """

    model_config = ConfigDict(extra="forbid", use_attribute_docstrings=True)


class Document(Contract):
    """会被持久化或对外导出的顶层对象。

    子类必须声明 `schema_version: Literal[<n>] = <n>`。改动不向后兼容时（删除或重命名字段、
    把可选字段改为必填、收紧取值范围、删除枚举值、改变含义），把版本号加 1，并为旧数据写迁移。
    新增可选字段或枚举值不需要加。见 docs/architecture.md 的“约定”。
    """
