"""来信后自动分析和起草（#8）：客服打开案件时草稿已经准备好，不需要手动开始。

- **找出要起草的案件**（`scan`）：未结案（待处理、待判断、已升级）、并且最新一封玩家邮件还没有
  草稿的案件，先进先出。来信（模拟邮箱、以后的 #10）后立即唤醒扫描，另外每隔几秒扫描一次，
  所以服务启动前就在库中的案件（例如 `copilot seed`）也会被起草。
- **起草**（`run_job`）：读案件、调用起草服务（LLM 调用不在数据库事务中）、写回结果，各用一个
  短事务。写回前重新读案件：玩家在起草期间又来信、或案件已结案时丢弃结果，下一次扫描会按
  新的来信起草。
- **自动发送**（`WORKBENCH_AUTO_SEND`，D-018）：开启时，分流为 `auto_send` 的草稿在写回的同一个
  事务中由系统发送（进入发件箱）。是否 `auto_send` 完全由 #6 的分流决定。
- **失败**：记录原因，不自动重试（避免对同一封来信反复调用 LLM）。客服可以在界面中手动生成；
  玩家再次来信时会重新起草。
- 状态只保存在进程内存中：重启后失败记录清空，未完成的案件会重新起草。
"""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import Engine

from copilot.contracts import CaseStatus, FeedbackAction, Message, MessageRole, Route
from copilot.storage import SqlRepository, session_scope
from copilot.storage.repository import Clock, utc_now
from copilot.workbench.drafting import Drafter, DrafterFailedError, DrafterUnavailableError
from copilot.workbench.service import CLOSED_STATUSES, apply_outcome, submit_feedback

logger = logging.getLogger(__name__)

AUTO_SEND_ACTOR = "系统（自动发送）"
"""自动发送的回复在 `Feedback.actor` 和 `OutboundMail.sent_by` 中的名字。"""

SCAN_STATUSES = frozenset({CaseStatus.OPEN, CaseStatus.UNDETERMINED, CaseStatus.ESCALATED})
SCAN_INTERVAL_SECONDS = 3.0
SCAN_LIMIT = 200

JobStateName = Literal["queued", "running", "failed"]


@dataclass(frozen=True)
class JobState:
    """一个案件的自动起草状态。`message_seq` 是要回复的玩家邮件。"""

    state: JobStateName
    message_seq: int
    updated_at: datetime
    error: str | None = None


def _latest_player_seq(messages: list[Message]) -> int | None:
    seqs = [m.seq for m in messages if m.role is MessageRole.PLAYER]
    return max(seqs) if seqs else None


class AutoDrafter:
    """后台自动起草。`start()` 启动扫描线程和起草线程池；测试中可以直接调用 `scan` 和 `run_job`。"""

    def __init__(
        self,
        engine: Engine,
        drafter: Drafter,
        *,
        workers: int = 2,
        auto_send: bool = False,
        clock: Clock = utc_now,
    ) -> None:
        self._engine = engine
        self._drafter = drafter
        self._workers = workers
        self._auto_send = auto_send
        self._clock = clock
        self._jobs: dict[str, JobState] = {}
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._executor: ThreadPoolExecutor | None = None
        self._thread: threading.Thread | None = None

    # --- 状态 ---

    def status(self, case_id: str) -> JobState | None:
        with self._lock:
            return self._jobs.get(case_id)

    def busy(self, case_id: str) -> bool:
        """案件是否正在排队或起草。此时不允许手动起草，避免同一封来信起草两次。"""
        job = self.status(case_id)
        return job is not None and job.state != "failed"

    def forget(self, case_id: str) -> None:
        """清除失败记录（例如客服手动起草成功后）。"""
        with self._lock:
            job = self._jobs.get(case_id)
            if job is not None and job.state == "failed":
                del self._jobs[case_id]

    def _set(self, case_id: str, job: JobState | None) -> None:
        with self._lock:
            if job is None:
                self._jobs.pop(case_id, None)
            else:
                self._jobs[case_id] = job

    # --- 扫描与起草 ---

    def scan(self) -> list[tuple[str, int]]:
        """找出需要起草的案件并标为排队，返回（案件 ID, 玩家邮件 seq）。"""
        found: list[tuple[str, int]] = []
        with session_scope(self._engine) as session:
            repo = SqlRepository(session)
            page = repo.list_cases(statuses=SCAN_STATUSES, sort="oldest", limit=SCAN_LIMIT)
            for case in page.items:
                seq = _latest_player_seq(case.messages)
                if seq is None:
                    continue
                draft = repo.latest_draft(case.id)
                if draft is not None and draft.message_seq == seq:
                    continue
                job = self.status(case.id)
                if job is not None and job.message_seq == seq:
                    continue  # 已在排队、起草中，或这封来信已经失败过
                found.append((case.id, seq))
        for case_id, seq in found:
            self._set(case_id, JobState("queued", seq, self._clock()))
        return found

    def run_job(self, case_id: str, seq: int) -> None:
        """起草一个案件并写回结果。异常记录为失败，不向外抛出。"""
        self._set(case_id, JobState("running", seq, self._clock()))
        try:
            with session_scope(self._engine) as session:
                case = SqlRepository(session).get_case(case_id)
            if case is None or case.status in CLOSED_STATUSES:
                self._set(case_id, None)
                return
            if _latest_player_seq(case.messages) != seq:
                self._set(case_id, None)  # 又有来信：交给下一次扫描
                return
            outcome = self._drafter.draft(case, background=True)
            with session_scope(self._engine) as session:
                repo = SqlRepository(session)
                current = repo.get_case(case_id)
                if (
                    current is None
                    or current.status in CLOSED_STATUSES
                    or _latest_player_seq(current.messages) != seq
                ):
                    logger.info("自动起草：%s 在起草期间有变化，丢弃结果", case_id)
                    self._set(case_id, None)
                    return
                apply_outcome(repo, current, outcome)
                draft = outcome.draft
                if self._auto_send and draft is not None and draft.route is Route.AUTO_SEND:
                    submit_feedback(
                        repo,
                        draft.id,
                        actor=AUTO_SEND_ACTOR,
                        action=FeedbackAction.APPROVED,
                        drafter=self._drafter,
                        reason="分流为可直接发送，系统自动发送（WORKBENCH_AUTO_SEND）",
                    )
                    logger.info("自动发送：%s", case_id)
            self._set(case_id, None)
        except (DrafterUnavailableError, DrafterFailedError, ValueError) as exc:
            self._fail(case_id, seq, str(exc))
        except Exception:  # 后台线程：任何异常都只记录，不能让线程退出
            logger.exception("自动起草失败：%s", case_id)
            self._fail(case_id, seq, "内部错误，详见服务日志")

    def _fail(self, case_id: str, seq: int, error: str) -> None:
        logger.warning("自动起草失败：%s：%s", case_id, error)
        self._set(case_id, JobState("failed", seq, self._clock(), error))

    # --- 线程 ---

    def wake(self) -> None:
        """有新来信时立即扫描，不等下一个周期。"""
        self._wake.set()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._executor = ThreadPoolExecutor(self._workers, thread_name_prefix="auto-draft")
        self._thread = threading.Thread(target=self._loop, name="auto-draft-scan", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        if self._executor is not None:
            # 不等正在进行的 LLM 调用结束；它们的结果在重启后会重新生成
            self._executor.shutdown(wait=False, cancel_futures=True)

    def _loop(self) -> None:
        executor = self._executor
        assert executor is not None
        while not self._stop.is_set():
            try:
                for case_id, seq in self.scan():
                    executor.submit(self.run_job, case_id, seq)
            except Exception:
                logger.exception("自动起草：扫描失败")
            self._wake.wait(SCAN_INTERVAL_SECONDS)
            self._wake.clear()
