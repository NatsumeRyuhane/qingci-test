"""坐席流程的服务函数。只依赖 `Repository` 和 `Drafter` 接口。"""

from copilot.contracts.enums import CaseStatus
from copilot.draft import Drafter, DraftResult
from copilot.storage import NotFoundError, Repository

CLOSED_STATUSES = frozenset({CaseStatus.REPLIED, CaseStatus.NO_REPLY_NEEDED})
"""已结案的状态。玩家追问会让案件回到 `open`，在那之前不能再起草或处理。"""


class ConflictError(RuntimeError):
    """请求与案件当前的状态冲突，例如为已回复的案件起草。"""


def generate_draft(
    repo: Repository, drafter: Drafter, case_id: str, *, template_id: str | None = None
) -> DraftResult:
    """为案件最新一封玩家邮件（重新）分析并起草，保存结果。

    案件变为 `drafted`，类别更新为分析得出的类别；已升级的案件保持 `escalated`，
    重新起草不会撤销升级。
    """
    case = repo.get_case(case_id)
    if case is None:
        raise NotFoundError(f"案件不存在：{case_id!r}")
    if case.status in CLOSED_STATUSES:
        raise ConflictError(f"案件已结案（{case.status.value}），不能起草")
    try:
        result = drafter.draft(case, template_id=template_id)
    except ValueError as exc:  # 例如线程中没有玩家邮件
        raise ConflictError(str(exc)) from exc

    repo.add_analysis(result.analysis)
    repo.add_draft(result.draft)
    # `Case.category` 优先采用分析结果（见契约）
    update: dict[str, object] = {"category": result.analysis.category}
    if case.status is not CaseStatus.ESCALATED:
        update["status"] = CaseStatus.DRAFTED
    repo.save_case(case.model_copy(update=update))
    return result
