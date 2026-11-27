"""`Drafter` 的真实实现：调用 #6 的起草流水线 `draft_case`。

与 `copilot draft` 命令使用同样的输入：
- 知识库：数据库中已发布的条目（`copilot kb publish`），**每次起草时重新读取**，发布新版本后
  不用重启服务
- 风格指南：`knowledge/`
- 历史案件（确认重复提交）：导入结果 `data/interim/cases.jsonl`，没有时为空
- 相似案件的语料：只用 train（D-012），没有切分结果时用全部导入结果；只取“现在”之前的线程
- 时间：“现在”取系统时间（生产模式），知识库不按 `known_from` 过滤

样本和切分结果在第一次起草时读取并缓存。
"""

import asyncio
import logging
import threading
from collections.abc import Callable, Coroutine
from datetime import datetime
from pathlib import Path
from typing import Any

import openai

from copilot.config import LLMTask, MissingConfigError, Settings
from copilot.contracts import Case, TimeContext, TimePoint, TimeStatus
from copilot.draft.compose import back_translate
from copilot.draft.retrieval import KnowledgeSnapshot, build_view, load_snapshot
from copilot.draft.service import DraftOutcome, DraftParams, draft_case
from copilot.draft.similar import EmbeddingCache, SimilarCaseRetriever, corpus_before
from copilot.draft.timectx import build_now
from copilot.eval import DEFAULT_MANIFEST, Split, StaleSplitError, read_manifest
from copilot.ingest import DEFAULT_OUTPUT, read_cases
from copilot.ingest.timestamps import JST
from copilot.kb.knowledge import KNOWLEDGE_DIR, KnowledgeError, StyleGuide, load_knowledge
from copilot.kb.store import KnowledgeStore
from copilot.llm import (
    EmbeddingClient,
    LLMClient,
    LLMOutputError,
    OpenAICompatibleClient,
    OpenAIEmbeddingClient,
)
from copilot.storage import create_engine_for
from copilot.workbench.drafting import DrafterFailedError, DrafterUnavailableError

logger = logging.getLogger(__name__)

EMBEDDING_CACHE = Path("data/processed/embeddings.jsonl")
"""与 `copilot draft` 共用的 embedding 缓存（被 git 忽略）。"""

_LLM_ERRORS = (LLMOutputError, openai.OpenAIError)


def _now() -> datetime:
    return datetime.now(JST).replace(microsecond=0)


def _embedding_client(settings: Settings) -> EmbeddingClient | None:
    try:
        settings.embedding_endpoint()
    except MissingConfigError:
        return None
    return OpenAIEmbeddingClient(settings)


class PipelineDrafter:
    """调用 `draft_case` 的 `Drafter`。API 的端点是同步函数（运行在线程池中），这里用
    `asyncio.run` 执行一次异步的流水线。"""

    def __init__(
        self,
        settings: Settings,
        *,
        llm: LLMClient | None = None,
        embedder: EmbeddingClient | None = None,
        knowledge_dir: Path = KNOWLEDGE_DIR,
        cases_path: Path = DEFAULT_OUTPUT,
        split_path: Path = DEFAULT_MANIFEST,
        clock: Callable[[], datetime] = _now,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        """`clock` 决定生成时间；`now` 决定起草时的“现在”，默认（None）为系统时间，
        测试中可以固定。"""
        self._settings = settings
        self._llm = llm
        self._embedder = embedder if embedder is not None else _embedding_client(settings)
        self._knowledge_dir = knowledge_dir
        self._cases_path = cases_path
        self._split_path = split_path
        self._clock = clock
        self._now = now
        self._lock = threading.Lock()
        self._guide: StyleGuide | None = None
        self._history: list[Case] | None = None
        self._corpus: list[Case] | None = None
        self._store: KnowledgeStore | None = None

    # --- 资源 ---

    def _params(self) -> DraftParams:
        try:
            models = {
                task: self._settings.llm_endpoint(task).model
                for task in (LLMTask.ANALYZE, LLMTask.DRAFT, LLMTask.BACKTRANSLATE)
            }
        except MissingConfigError as exc:
            raise DrafterUnavailableError(str(exc)) from exc
        return DraftParams(
            analyze_model=models[LLMTask.ANALYZE],
            draft_model=models[LLMTask.DRAFT],
            min_confidence=self._settings.draft_auto_send_min_confidence,
            lookback_days=self._settings.draft_problem_time_lookback_days,
            tools=self._settings.workbench_draft_tools,
            max_tool_steps=self._settings.draft_max_tool_steps,
        )

    def _client(self) -> LLMClient:
        if self._llm is None:
            self._llm = OpenAICompatibleClient(self._settings)
        return self._llm

    def _snapshot(self) -> KnowledgeSnapshot:
        if self._store is None:
            self._store = KnowledgeStore(create_engine_for(self._settings.database_url))
        snapshot = load_snapshot(self._store)
        if not snapshot.templates:
            raise DrafterUnavailableError("数据库中还没有模板（先运行 copilot kb publish）")
        return snapshot

    def _load(self) -> tuple[StyleGuide, list[Case], list[Case]]:
        with self._lock:
            if self._guide is None:
                try:
                    self._guide = load_knowledge(self._knowledge_dir).style_guide
                except KnowledgeError as exc:
                    raise DrafterUnavailableError(f"knowledge/ 校验失败：{exc}") from exc
            if self._history is None or self._corpus is None:
                history = read_cases(self._cases_path) if self._cases_path.is_file() else []
                corpus = history
                if self._split_path.is_file():
                    try:
                        corpus = read_manifest(self._split_path).select(history, Split.TRAIN)
                    except StaleSplitError as exc:
                        raise DrafterUnavailableError(str(exc)) from exc
                self._history, self._corpus = history, corpus
                logger.info(
                    "起草服务：历史案件 %d 个，相似案件语料 %d 个", len(history), len(corpus)
                )
            return self._guide, self._history, self._corpus

    def _run[T](self, coroutine: Coroutine[Any, Any, T]) -> T:
        try:
            return asyncio.run(coroutine)
        except _LLM_ERRORS as exc:
            # 只记录异常类型：LLM 的报错中可能带有邮件内容
            logger.warning("起草服务调用失败：%s", type(exc).__name__)
            raise DrafterFailedError("起草服务调用失败，请稍后重试") from exc

    # --- Drafter ---

    def draft(self, case: Case) -> DraftOutcome:
        params = self._params()
        snapshot = self._snapshot()
        guide, history, corpus = self._load()
        cache = EmbeddingCache(EMBEDDING_CACHE) if self._embedder is not None else None
        retriever = SimilarCaseRetriever(
            corpus_before(corpus, case), embedder=self._embedder, cache=cache
        )
        return self._run(
            draft_case(
                case,
                snapshot=snapshot,
                guide=guide,
                llm=self._client(),
                params=params,
                history=history,
                retriever=retriever,
                now=self._now() if self._now is not None else None,
                created_at=self._clock(),
            )
        )

    def backtranslate(self, body_ja: str) -> str:
        self._params()  # 缺少模型配置时给出与起草相同的提示
        # 与起草时相同的术语表：现在适用的、已批准的版本
        now = TimeContext(
            now=build_now(self._now() if self._now is not None else None),
            problem_time=TimePoint(status=TimeStatus.UNAVAILABLE),
        )
        glossary = build_view(self._snapshot(), now, form=None, known_at=None).glossary
        return self._run(back_translate(body_ja, self._client(), glossary))
