"""可用知识与候选模板（#6，提案 0003 §4、§5、§9）。SYNTHETIC：全部是合成数据。"""

from datetime import timedelta

from copilot.contracts import (
    InquiryForm,
    Platform,
    ReviewStatus,
    TimeContext,
    TimePoint,
    TimeStatus,
)
from copilot.draft.retrieval import KnowledgeSnapshot, build_view, load_snapshot
from copilot.draft.timectx import WindowMatch
from copilot.ingest.timestamps import JST
from tests.draft.factories import (
    context,
    dt,
    forbidden,
    intent,
    known,
    notice,
    template,
    trigger,
)

NOW = dt(3, 5, 12)
INCIDENT = notice(start=dt(3, 2, 11), end=dt(3, 2, 14), template_ids=["tpl-syn-incident"])


def snapshot(**values: object) -> KnowledgeSnapshot:
    base: dict[str, object] = {
        "intents": [intent()],
        "templates": [
            template("tpl-syn-generic"),
            template("tpl-syn-incident", body="【合成データ】障害のお詫び"),
            template("tpl-syn-slot", body="【合成データ】{{item}}について", slots=["item"]),
            template("tpl-syn-other", intent_id="syn_other"),
        ],
        "notices": [INCIDENT],
        "policies": [forbidden(), trigger()],
    }
    return KnowledgeSnapshot(**{**base, **values})  # type: ignore[arg-type]


def test_only_approved_current_versions_are_visible() -> None:
    snap = snapshot(
        templates=[
            template("tpl-a", version=1),
            template("tpl-a", version=2, status=ReviewStatus.DRAFT),  # 最新版本未审核
            template("tpl-b", status=ReviewStatus.NEEDS_REREVIEW),
            template("tpl-c", version=1),
            template("tpl-c", version=2, status=ReviewStatus.RETIRED),
            template("tpl-d"),
        ]
    )
    view = build_view(snap, context(NOW))
    assert list(view.static_templates) == ["tpl-d"]


def test_expired_template_is_not_visible() -> None:
    snap = snapshot(templates=[template("tpl-old", valid_to=dt(3, 1)), template("tpl-new")])
    assert list(build_view(snap, context(NOW)).static_templates) == ["tpl-new"]


def test_replay_ignores_versions_not_yet_known() -> None:
    later = template("tpl-a", version=2, known_from=dt(3, 10), recorded_at=dt(3, 10), body="新")
    snap = snapshot(templates=[template("tpl-a", known_from=dt(1, 1), recorded_at=dt(1, 1)), later])
    replay = build_view(snap, context(NOW), known_at=NOW)
    assert replay.static_templates["tpl-a"].version == 1
    assert build_view(snap, context(NOW)).static_templates["tpl-a"].version == 2


def test_time_bound_template_never_in_static_catalog() -> None:
    # 问题发生时间在事故窗口外：事故模板不注入，也不出现在静态目录中。
    view = build_view(snapshot(), context(NOW, dt(3, 2, 20)))
    assert "tpl-syn-incident" not in view.static_templates
    assert view.time_bound_templates == {}
    assert view.notices == []
    assert [c.template.id for c in view.candidates("syn_bug")] == [
        "tpl-syn-slot",
        "tpl-syn-generic",
    ]


def test_template_linked_by_retired_notice_stays_time_bound() -> None:
    retired = notice(
        start=dt(3, 2, 11), template_ids=["tpl-syn-incident"], version=2, review_status="retired"
    )
    view = build_view(snapshot(notices=[INCIDENT, retired]), context(NOW, dt(3, 2, 12)))
    assert "tpl-syn-incident" not in view.static_templates
    assert view.time_bound_templates == {}


def test_matching_incident_injects_template_first() -> None:
    view = build_view(snapshot(), context(NOW, dt(3, 2, 12)))
    assert [n.notice.id for n in view.notices] == ["syn-incident"]
    candidates = view.candidates("syn_bug")
    assert [c.template.id for c in candidates] == [
        "tpl-syn-incident",
        "tpl-syn-slot",
        "tpl-syn-generic",
    ]
    first = candidates[0]
    assert first.time_bound and first.specific and first.window is WindowMatch.INSIDE
    assert not candidates[-1].specific


def test_time_of_day_only_can_be_uncertain() -> None:
    # 只有时刻 12:30：回看区间内有 3 月 2 日（窗口内）也有其他日子（窗口外）。
    problem = TimePoint(
        status=TimeStatus.ESTIMATED,
        earliest=NOW - timedelta(days=7),
        latest=NOW,
        time_of_day=dt(1, 1, 12, 30).timetz(),
    )
    candidate = build_view(snapshot(), context(NOW, problem)).candidates("syn_bug")[0]
    assert candidate.template.id == "tpl-syn-incident"
    assert candidate.window is WindowMatch.UNCERTAIN


def test_time_of_day_outside_window_excludes_incident() -> None:
    problem = TimePoint(
        status=TimeStatus.ESTIMATED,
        earliest=NOW - timedelta(days=7),
        latest=NOW,
        time_of_day=dt(1, 1, 20).timetz().replace(tzinfo=JST),
    )
    view = build_view(snapshot(), context(NOW, problem))
    assert view.time_bound_templates == {}


def test_unavailable_problem_time_is_uncertain() -> None:
    candidate = build_view(snapshot(), context(NOW)).candidates("syn_bug")[0]
    assert candidate.template.id == "tpl-syn-incident"
    assert candidate.window is WindowMatch.UNCERTAIN


def test_platform_mismatch_excludes_notice() -> None:
    ios_only = notice(
        start=dt(3, 2, 11), end=dt(3, 2, 14), template_ids=["tpl-syn-incident"],
        platforms=[Platform.IOS],
    )  # fmt: skip
    form = InquiryForm(platform=Platform.GOOGLE_PLAY)
    view = build_view(snapshot(notices=[ios_only]), context(NOW, dt(3, 2, 12)), form=form)
    assert view.notices == []


def test_incident_template_of_other_intent_joins_via_notice_intents() -> None:
    linked = notice(
        start=dt(3, 2, 11), end=dt(3, 2, 14), template_ids=["tpl-syn-incident"],
        intents=["syn_purchase"],
    )  # fmt: skip
    view = build_view(snapshot(notices=[linked]), context(NOW, dt(3, 2, 12)))
    assert [c.template.id for c in view.candidates("syn_purchase")] == ["tpl-syn-incident"]


def test_candidate_outside_intent_is_marked() -> None:
    view = build_view(snapshot(), context(NOW))
    other = view.candidate("tpl-syn-other", "syn_bug")
    assert other is not None and not other.in_intent
    assert view.candidate("tpl-missing", "syn_bug") is None


def test_policies_split_by_kind() -> None:
    view = build_view(snapshot(), context(NOW))
    assert [p.id for p in view.forbidden_promises] == ["syn-no-refund"]
    assert [p.id for p in view.escalation_triggers] == ["syn-legal"]


def test_load_snapshot_reads_every_type() -> None:
    class Store:
        def entries(self, model: type) -> list[object]:
            return {"ReplyTemplate": [template()], "Intent": [intent()]}.get(model.__name__, [])

    snap = load_snapshot(Store())  # type: ignore[arg-type]
    assert len(snap.templates) == 1 and len(snap.intents) == 1 and snap.notices == []


# --- 评审意见（PR #28）-------------------------------------------------------


def test_replay_ignores_notice_links_added_later() -> None:
    early = {"known_from": dt(1, 1), "recorded_at": dt(1, 1)}
    v1 = notice(start=dt(3, 2, 11), end=dt(3, 2, 14), **early)
    v2 = notice(
        start=dt(3, 2, 11), end=dt(3, 2, 14), template_ids=["tpl-syn-incident"], version=2,
        known_from=dt(3, 10), recorded_at=dt(3, 10),
    )  # fmt: skip
    snap = snapshot(notices=[v1, v2], templates=[template("tpl-syn-incident", **early)])
    replay = build_view(snap, context(NOW, dt(3, 2, 12)), known_at=NOW)
    assert list(replay.static_templates) == ["tpl-syn-incident"]
    # 不重放时 v2 已知：模板是限时模板。
    assert "tpl-syn-incident" not in build_view(snap, context(NOW, dt(3, 2, 12))).static_templates


def test_uncertain_notice_validity_keeps_candidate_uncertain() -> None:
    # “现在”估计在 13:00–15:00 之间，公告在 14:00 失效；问题发生时间确定在事件窗口内。
    expiring = notice(
        start=dt(3, 5, 11), end=dt(3, 5, 12), template_ids=["tpl-syn-incident"],
        valid_to=dt(3, 5, 14),
    )  # fmt: skip
    now = TimePoint(status=TimeStatus.ESTIMATED, earliest=dt(3, 5, 13), latest=dt(3, 5, 15))
    ctx = TimeContext(now=now, problem_time=known(dt(3, 5, 11, 30)))
    view = build_view(snapshot(notices=[expiring]), ctx)
    assert view.notices[0].match is WindowMatch.UNCERTAIN
    assert view.candidates("syn_bug")[0].window is WindowMatch.UNCERTAIN
