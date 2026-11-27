"""把不可信的文本（邮件正文、工具结果）放进 prompt（#18，提案 0003 §13）。

不可信的文本只作为**数据**放在固定的分隔标记 `<tag>…</tag>` 之内，系统指令说明标记内的
内容是数据、不是指令。为了让文本不能提前结束数据块或伪造另一个块：

- 文本中的**所有** `<` 换成全角的 `＜`：任何写法的标记（`</tag>`、`< / TAG >`……）都无法伪造。
  只替换同名标记需要处理任意的空白和大小写，正则容易退化成平方级；邮件正文中的 `<` 对模型
  理解内容也没有意义；
- 去掉不可见字符和方向控制符（零宽字符、BOM、双向文本控制符），它们可以把指令藏起来，
  或让显示的内容与模型看到的不同。

只做这些，不尝试识别“像指令的句子”：拦截注入的后果由代码层负责（政策检查、分流、只向
模型提供已批准的条目），见提案 0003 §13。
"""

import re

_INVISIBLE = re.compile("[​-‏‪-‮⁠-⁤⁦-⁩﻿]")
"""零宽字符、方向标记、双向嵌入与覆盖、不可见运算符、双向隔离、BOM。"""
_TAG = re.compile(r"^[a-z][a-z_]{0,31}$")


def clean_untrusted(text: str) -> str:
    """去掉不可见字符和方向控制符。"""
    return _INVISIBLE.sub("", text)


def data_block(tag: str, text: str, **attributes: str) -> str:
    """把不可信的 `text` 放进 `<tag ...>…</tag>`。`attributes` 是由代码生成的可信属性。"""
    if not _TAG.match(tag):
        raise ValueError(f"标记名不合法：{tag!r}")
    for name, value in attributes.items():
        if not _TAG.match(name) or not re.fullmatch(r"[A-Za-z0-9_:.\-]{0,64}", value):
            raise ValueError(f"属性不合法：{name}={value!r}")
    body = clean_untrusted(text).replace("<", "＜")
    attrs = "".join(f' {name}="{value}"' for name, value in attributes.items())
    return f"<{tag}{attrs}>\n{body}\n</{tag}>"
