"""草稿的组装与代码自检（#6，提案 0003 §2、§9、§10、§13）。

自检**全部由代码执行**，不依赖模型自觉遵守；**假设模型已被攻破**（提案 0003 §13）。
模型输出的只是正文的“答复”部分（`core_ja`）和引用，固定结构由代码按风格指南组装：

    问候 →（致歉）→ 答复 →（请再次联系）→ 结语 → 禁止转载声明

**模板原文与新增文字分开检查。** 已批准的模板正文（填入占位符后）是所有者审核过的措辞，
其中出现的政策短语（例如事故公告中写明已发放的补偿）不算模型做出的承诺；模型在模板之外
新增的文字（`added_text`）才按政策、链接、其他案件的信息检查。模型改写了模板原文时，
整段答复都按新增文字处理，结果只会更严格。
"""

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from copilot.contracts import (
    PLACEHOLDER_PATTERN,
    DraftCheck,
    DraftCheckKind,
    EntryType,
    Policy,
)
from copilot.draft.retrieval import Candidate
from copilot.draft.timectx import WindowMatch
from copilot.kb.knowledge import StyleGuide
from copilot.llm.prompt import clean_untrusted

FOREIGN_MIN_CHARS = 20
"""草稿中与其他案件的文字相同的一段至少这么长，才判为泄露了其他案件的信息。"""

_SENTENCE = re.compile(r"[^。！？!?\n]+[。！？!?]?")
_POLITE_END = re.compile(
    r"(ます|ません|です|でした|ました|ましょう|ください|下さい|ございます|ございません|"
    r"でしょうか|いたします|致します|願います|ませ)$"
)
_TRAILING = re.compile(r"[\s。！？!?」』）)】〕…・〜ー]+$")
_URL = re.compile(r"https?://|www\.", re.IGNORECASE)
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_HTML = re.compile(r"[<＜]\s*/?\s*[A-Za-z!]")
_SPACE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """比较用：NFKC、去掉所有空白。"""
    return _SPACE.sub("", unicodedata.normalize("NFKC", text))


def sentences(text: str) -> list[str]:
    """按句号、问号、感叹号和换行切分，去掉空白，丢弃空句。"""
    return [s.strip() for s in _SENTENCE.findall(text) if s.strip()]


def fill_slots(body: str, values: dict[str, str]) -> str:
    """把 `{{name}}` 换成 `values[name]`；没有值的占位符保持原样，由 `slots_filled` 检查发现。"""

    def replace(match: re.Match[str]) -> str:
        value = values.get(match.group(1))
        return match.group(0) if value is None or not value.strip() else value.strip()

    return PLACEHOLDER_PATTERN.sub(replace, body)


def assemble(
    guide: StyleGuide,
    core_ja: str,
    *,
    apology_ja: str | None = None,
    contact_again_ja: str | None = None,
) -> str:
    """按风格指南组装全文。答复中已经有这句致歉或“请再次联系”时不重复添加。"""
    core = core_ja.strip()
    parts = [guide.greeting.ja]
    if apology_ja and normalize(apology_ja) not in normalize(core):
        parts.append(apology_ja)
    parts.append(core)
    if contact_again_ja and normalize(contact_again_ja) not in normalize(core):
        parts.append(contact_again_ja)
    parts.append(f"{guide.closing.ja}\n{guide.notice.ja}")
    return "\n\n".join(parts)


@dataclass(frozen=True)
class CitedClaim:
    """模型给出的一条引用：草稿中的哪一句（`sentence_ja`）引用了哪个条目。"""

    entry_type: EntryType
    entry_id: str
    entry_version: int
    claim_zh: str
    sentence_ja: str = ""


@dataclass(frozen=True)
class ComposedDraft:
    """待检查的草稿。"""

    body_ja: str
    """组装后的全文。"""
    core_ja: str
    """模型写的答复部分。"""
    candidate: Candidate | None
    """使用的模板；没有合适的模板时为空。"""
    template_text_ja: str = ""
    """模板正文填入占位符后的文字（未填的占位符保持原样）。"""
    citations: Sequence[CitedClaim] = ()
    info_requests_ja: Sequence[str] = ()
    """模型声明为“向玩家索要信息”的句子：不是事实性说法，不需要引用。"""

    @property
    def added_text(self) -> str:
        """模型在模板和固定结构之外新增的文字（见模块说明）。"""
        core = self.core_ja
        if self.template_text_ja and self.template_text_ja.strip() in core:
            core = core.replace(self.template_text_ja.strip(), "\n")
        return core

    def added_sentences(self, guide: StyleGuide) -> list[str]:
        """新增的句子：既不在模板原文中，也不是风格指南的固定措辞。"""
        known = normalize(self.template_text_ja) + "".join(
            normalize(p.ja) for p in (*guide.apologies, *guide.contact_again)
        )
        return [s for s in sentences(self.added_text) if normalize(s) not in known]


@dataclass(frozen=True)
class CheckContext:
    """检查需要的知识。"""

    guide: StyleGuide
    forbidden_promises: Sequence[Policy] = ()
    provided_entries: frozenset[tuple[EntryType, str, int]] = frozenset()
    """提供给模型的条目（类型, id, version）：静态目录、时间块和工具结果中出现过的。"""
    foreign_texts: Sequence[str] = field(default_factory=tuple)
    """其他案件的文字（例如检索到的相似案件），草稿中不能出现。"""


def _check(kind: DraftCheckKind, problems: list[str], ok: str | None = None) -> DraftCheck:
    if problems:
        return DraftCheck(kind=kind, passed=False, detail_zh="；".join(problems))
    return DraftCheck(kind=kind, passed=True, detail_zh=ok)


def check_structure(draft: ComposedDraft, guide: StyleGuide) -> DraftCheck:
    problems = []
    body = draft.body_ja
    ending = f"{guide.closing.ja}\n{guide.notice.ja}"
    if not body.startswith(guide.greeting.ja):
        problems.append("开头不是固定的问候语")
    if not body.rstrip().endswith(ending):
        problems.append("结尾不是固定的结语和禁止转载声明")
    for name, phrase in (("问候语", guide.greeting.ja), ("结语", guide.closing.ja)):
        if body.count(phrase) > 1:
            problems.append(f"{name}重复出现")
    if not draft.core_ja.strip():
        problems.append("答复部分为空")
    return _check(DraftCheckKind.STRUCTURE, problems)


def check_register(draft: ComposedDraft, guide: StyleGuide) -> DraftCheck:
    """新增的、以句号等结束的句子必须是敬体。没有句末标点的短行（例如列表项）不检查。"""
    plain = []
    for sentence in draft.added_sentences(guide):
        if not re.search(r"[。！？!?]$", sentence):
            continue
        if not _POLITE_END.search(_TRAILING.sub("", sentence)):
            plain.append(sentence)
    problems = [f"{len(plain)} 句新增的句子不是敬体"] if plain else []
    return _check(DraftCheckKind.REGISTER, problems)


def _phrases(policies: Iterable[Policy], text: str) -> list[str]:
    return [f"《{p.title_zh}》：{phrase}" for p in policies for phrase in p.matching_phrases(text)]


def check_forbidden_promise(draft: ComposedDraft, context: CheckContext) -> DraftCheck:
    added = _phrases(context.forbidden_promises, draft.added_text)
    if added:
        return _check(DraftCheckKind.FORBIDDEN_PROMISE, [f"新增文字中出现 {', '.join(added)}"])
    in_template = _phrases(context.forbidden_promises, draft.template_text_ja)
    note = f"只在已批准的模板原文中出现：{', '.join(in_template)}" if in_template else None
    return _check(DraftCheckKind.FORBIDDEN_PROMISE, [], note)


def check_template_window(draft: ComposedDraft) -> DraftCheck:
    candidate = draft.candidate
    if candidate is None:
        return _check(DraftCheckKind.TEMPLATE_WINDOW, [], "没有使用模板")
    if candidate.window is WindowMatch.INSIDE:
        ok = "限时模板，事件与问题发生时间匹配" if candidate.time_bound else "模板在有效期内"
        return _check(DraftCheckKind.TEMPLATE_WINDOW, [], ok)
    if candidate.window is WindowMatch.UNCERTAIN:
        what = "问题发生时间是否在事件窗口内" if candidate.time_bound else "模板此时是否有效"
        return _check(DraftCheckKind.TEMPLATE_WINDOW, [f"无法确定{what}"])
    return _check(DraftCheckKind.TEMPLATE_WINDOW, ["模板不适用于本案件的时间"])


def check_slots(draft: ComposedDraft) -> DraftCheck:
    left = sorted(set(PLACEHOLDER_PATTERN.findall(draft.body_ja)))
    problems = [f"占位符未填写：{', '.join(left)}"] if left else []
    return _check(DraftCheckKind.SLOTS_FILLED, problems)


def check_citations(draft: ComposedDraft, context: CheckContext) -> DraftCheck:
    problems = []
    unknown = sorted(
        f"{c.entry_type.value}:{c.entry_id}@{c.entry_version}"
        for c in draft.citations
        if (c.entry_type, c.entry_id, c.entry_version) not in context.provided_entries
    )
    if unknown:
        problems.append(f"引用了没有提供给模型的条目：{', '.join(unknown)}")
    covered = [normalize(c.sentence_ja) for c in draft.citations if c.sentence_ja.strip()]
    requests = [normalize(s) for s in draft.info_requests_ja if s.strip()]
    uncited = [
        s
        for s in draft.added_sentences(context.guide)
        if not any(normalize(s) in ref or ref in normalize(s) for ref in (*covered, *requests))
    ]
    if uncited:
        problems.append(f"{len(uncited)} 句新增的说法没有引用")
    return _check(DraftCheckKind.CITATIONS, problems)


def check_content_safety(draft: ComposedDraft, context: CheckContext) -> DraftCheck:
    added = draft.added_text
    problems = []
    if _URL.search(added):
        problems.append("新增文字中有链接")
    if _EMAIL.search(added):
        problems.append("新增文字中有邮箱地址")
    if _HTML.search(draft.body_ja):
        problems.append("有 HTML 或脚本标记")
    if clean_untrusted(draft.body_ja) != draft.body_ja:
        problems.append("有不可见字符或方向控制符")
    norm = normalize(added)
    for text in context.foreign_texts:
        other = normalize(text)
        if any(
            other[i : i + FOREIGN_MIN_CHARS] in norm
            for i in range(len(other) - FOREIGN_MIN_CHARS + 1)
        ):
            problems.append("含有其他案件中的文字")
            break
    return _check(DraftCheckKind.CONTENT_SAFETY, problems)


def run_checks(draft: ComposedDraft, context: CheckContext) -> list[DraftCheck]:
    """全部自检，按固定顺序。"""
    return [
        check_structure(draft, context.guide),
        check_register(draft, context.guide),
        check_forbidden_promise(draft, context),
        check_template_window(draft),
        check_slots(draft),
        check_citations(draft, context),
        check_content_safety(draft, context),
    ]
