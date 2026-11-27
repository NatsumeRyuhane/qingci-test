"""模拟邮箱的收件箱：一封玩家来信进入坐席流程（#8）。

演示用，代替真实的邮件系统。以后 #10 的来信接口可以复用 `receive_mail`。
"""

import uuid

from copilot.contracts import Case, InquiryForm, Message
from copilot.contracts.enums import CaseStatus, MessageRole
from copilot.ingest.text import fix_chars, normalize_newlines, normalize_whitespace, split_quote
from copilot.storage import NotFoundError, Repository
from copilot.storage.repository import Clock, utc_now
from copilot.workbench.service import InvalidRequestError

SIM_CASE_PREFIX = "SIM-"
"""模拟邮箱新建的线程 ID 的前缀，与样本的数字 ID 区分开。"""


def _new_case_id(repo: Repository) -> str:
    while True:
        case_id = f"{SIM_CASE_PREFIX}{uuid.uuid4().hex[:8].upper()}"
        if repo.get_case(case_id) is None:
            return case_id


def inbound_message(thread_id: str, seq: int, raw_body: str, clock: Clock) -> Message:
    """把一封来信清洗成 `Message`，清洗步骤与导入（#4）相同：去除引用、修复乱码、规范空白。

    来信一律记为玩家邮件：它是从外部发给我们的，不按正文判断角色，所以正文中写了客服的
    问候语也不会被当作客服回复（#18 第 5 项）。
    """
    split = split_quote(normalize_newlines(raw_body))
    fixed, _ = fix_chars(split.head)
    return Message(
        thread_id=thread_id,
        seq=seq,
        role=MessageRole.PLAYER,
        raw_body=raw_body,
        body=normalize_whitespace(fixed),
        quoted=split.quoted,
        sent_at=clock(),
    )


def receive_mail(
    repo: Repository,
    *,
    body: str,
    case_id: str | None = None,
    form: InquiryForm | None = None,
    clock: Clock = utc_now,
) -> Case:
    """投递一封玩家来信。

    - 不指定 `case_id`：新建一个线程，案件为 `open`。可以附带游戏内表单。
    - 指定 `case_id`：对已有案件的追问，追加一封玩家邮件。案件回到 `open`，之前的草稿作废；
      已升级的案件保持 `escalated`，仍由高级客服处理。追问不能附带表单（表单只在第 1 封）。
    """
    if not body.strip():
        raise InvalidRequestError("来信正文不能为空")
    if case_id is None:
        new_id = _new_case_id(repo)
        case = Case(
            id=new_id,
            messages=[inbound_message(new_id, 1, body, clock)],
            form=form,
            category=form.category if form else None,
        )
    else:
        if form is not None:
            raise InvalidRequestError("追问不能附带表单，表单只在线程的第 1 封邮件中")
        existing = repo.get_case(case_id)
        if existing is None:
            raise NotFoundError(f"案件不存在：{case_id!r}")
        message = inbound_message(case_id, existing.messages[-1].seq + 1, body, clock)
        status = (
            CaseStatus.ESCALATED if existing.status is CaseStatus.ESCALATED else CaseStatus.OPEN
        )
        case = Case.model_validate(
            existing.model_dump() | {"messages": [*existing.messages, message], "status": status}
        )
    repo.save_case(case)
    return case
