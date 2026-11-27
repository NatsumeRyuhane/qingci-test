"""识别重复提交的线程：同一个咨询被提交了多次（提案 0003 §8、§14）。

样本中账号 ID 和邮箱已脱敏，只能按内容判断：表单的咨询内容、设备、App 版本和问题发生时间
都相同，就认为是同一个咨询。样本中这样的线程 ID 距离中位数为 1（docs/data.md）。
生产环境中应按账号或发件地址判断，避免把另一位玩家的相同咨询当作重复。

用途：
- 切分（#5、#7）：同一组重复提交放在切分的同一侧，否则同一个咨询会同时出现在训练和评测中；
- “不回复”的标注（#5）：重复提交且对应的线程已回复。

全部是确定性的规则，不调用 LLM。
"""

import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable

from copilot.contracts import Case
from copilot.ingest.pipeline import thread_order

MIN_CONTENT_CHARS = 20
"""咨询内容（规范化后）少于这么多字符时不比较：太短的内容（例如“不具合です”）在不同玩家
之间也会相同。"""

_SPACE = re.compile(r"\s+")

type DuplicateKey = tuple[str, str | None, str | None, str | None]


def duplicate_key(case: Case) -> DuplicateKey | None:
    """比较重复提交用的键；没有表单或咨询内容太短时为 None（不参与比较）。"""
    form = case.form
    if form is None or form.content is None:
        return None
    content = _SPACE.sub("", unicodedata.normalize("NFKC", form.content))
    if len(content) < MIN_CONTENT_CHARS:
        return None
    return (content, form.device, form.app_version, form.occurred_at)


def duplicate_groups(cases: Iterable[Case]) -> list[list[str]]:
    """重复提交的线程组，每组至少两个线程。组内和组之间都按线程顺序（`thread_order`）排列，
    结果只取决于输入内容，与输入顺序无关。"""
    groups: dict[DuplicateKey, list[str]] = defaultdict(list)
    for case in cases:
        key = duplicate_key(case)
        if key is not None:
            groups[key].append(case.id)
    found = [sorted(ids, key=thread_order) for ids in groups.values() if len(ids) > 1]
    return sorted(found, key=lambda ids: thread_order(ids[0]))
