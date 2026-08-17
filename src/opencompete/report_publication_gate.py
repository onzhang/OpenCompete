"""发布前对 Markdown 正文的确定性门禁。."""

from __future__ import annotations

import re
from collections.abc import Sequence

from pydantic import Field

from opencompete.models import FrozenModel

INTERNAL_SCHEMA_LEAK = "internal_schema_leak"
LITERAL_UNKNOWN_LEAK = "literal_unknown_leak"
MISSING_RESOLVABLE_SOURCES = "missing_resolvable_sources"
DECISION_SUMMARY_MISSING = "decision_summary_missing"
LANGUAGE_CONSISTENCY_FAILED = "language_consistency_failed"
EXCESSIVE_REPETITION = "excessive_repetition"

_INTERNAL_PATTERNS = (
    re.compile(r"\bproduct_service\b"),
    re.compile(r"\bcompany_organization\b"),
    re.compile(r"\bindustry_market\b"),
    re.compile(r"\bcapabilities\b"),
    re.compile(r"\buser_experience\b"),
    re.compile(r"\btechnology_integrations\b"),
    re.compile(r"\bservice_support\b"),
    re.compile(r"\bClaimStatus\b"),
    re.compile(r"\bsrc_[a-f0-9]{6,}\b", re.I),
    re.compile(r"\bevd_[a-f0-9]{6,}\b", re.I),
    re.compile(r"\bclm_[a-f0-9]{6,}\b", re.I),
    re.compile(r"\bcmp_[a-f0-9]{6,}\b", re.I),
    re.compile(r"冻结单元"),
)
_BARE_UNKNOWN = re.compile(r"(?<![A-Za-z])Unknown(?![A-Za-z])")
_CLICKABLE_LINK = re.compile(r"https?://[^\s)>\]]+", re.I)
_CJK = re.compile(r"[\u4e00-\u9fff]")
_DECISION_HINTS = (
    "建议",
    "适合",
    "选择",
    "推荐",
    "优先",
    "不建议",
    "结论",
)


# 强制阻断发布的错误（AC4：缺来源/缺决策摘要与泄漏类问题同级）。
HARD_FAIL_CODES = frozenset(
    {
        INTERNAL_SCHEMA_LEAK,
        LITERAL_UNKNOWN_LEAK,
        MISSING_RESOLVABLE_SOURCES,
        DECISION_SUMMARY_MISSING,
        EXCESSIVE_REPETITION,
        LANGUAGE_CONSISTENCY_FAILED,
    }
)


class PublicationGateResult(FrozenModel):
    """门禁判定结果。."""

    ok: bool
    error_codes: tuple[str, ...] = ()
    hints: tuple[str, ...] = ()

    @staticmethod
    def passed(codes: Sequence[str] = (), hints: Sequence[str] = ()) -> PublicationGateResult:
        """构造通过门禁的结果并去重保留提示。."""
        return PublicationGateResult(
            ok=True,
            error_codes=tuple(dict.fromkeys(codes)),
            hints=tuple(hints),
        )

    @staticmethod
    def failed(codes: Sequence[str], hints: Sequence[str] = ()) -> PublicationGateResult:
        """构造失败门禁的结果并去重保留提示。."""
        unique = tuple(dict.fromkeys(codes))
        return PublicationGateResult(
            ok=False,
            error_codes=unique,
            hints=tuple(hints),
        )


class PublicationResolution(FrozenModel):
    """发布路径决议：正文、是否 partial、错误码。."""

    markdown: str = Field(min_length=1)
    partial: bool = False
    error_codes: tuple[str, ...] = ()
    used_fallback: bool = False
    repair_attempted: bool = False


def evaluate_report_publication_gate(
    markdown: str,
    *,
    output_language: str = "zh-CN",
    decision_task: bool = True,
    decision_window_chars: int = 1500,
) -> PublicationGateResult:
    """对候选 Markdown 做确定性检查，不调用 LLM。."""
    text = markdown or ""
    codes: list[str] = []
    hints: list[str] = []

    for pattern in _INTERNAL_PATTERNS:
        if pattern.search(text):
            codes.append(INTERNAL_SCHEMA_LEAK)
            hints.append("移除内部枚举、内部 ID 与冻结单元措辞")
            break

    if _BARE_UNKNOWN.search(text):
        codes.append(LITERAL_UNKNOWN_LEAK)
        hints.append("将裸 Unknown 改写为用户可执行的核实说明")

    if not _CLICKABLE_LINK.search(text):
        codes.append(MISSING_RESOLVABLE_SOURCES)
        hints.append("为关键事实增加可点击的 https 来源链接")

    if decision_task:
        head = text[:decision_window_chars]
        if not any(hint in head for hint in _DECISION_HINTS):
            codes.append(DECISION_SUMMARY_MISSING)
            hints.append("在开篇 1500 字内给出选择结论摘要")

    if output_language.lower().startswith("zh"):
        # 中文任务：正文应有足够汉字；大段仅英文内部枚举视为失败
        cjk = len(_CJK.findall(text))
        latin = len(re.findall(r"[A-Za-z]", text))
        if len(text) > 120 and cjk < 30 and latin > cjk * 2:
            codes.append(LANGUAGE_CONSISTENCY_FAILED)
            hints.append("报告主体应使用中文")
        enum_hits = sum(1 for p in _INTERNAL_PATTERNS if p.search(text))
        if enum_hits >= 3 and cjk < 80 and latin > 80:
            if LANGUAGE_CONSISTENCY_FAILED not in codes:
                codes.append(LANGUAGE_CONSISTENCY_FAILED)

    if _has_excessive_repetition(text):
        codes.append(EXCESSIVE_REPETITION)
        hints.append("合并重复的同义章节与重复结论段")

    if codes:
        return PublicationGateResult.failed(codes, hints)
    return PublicationGateResult.passed()


def _has_excessive_repetition(text: str) -> bool:
    """检测大段重复或同义标题重复。."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    headings = [line for line in lines if line.startswith("#")]
    if len(headings) >= 4:
        normalized = [re.sub(r"\s+", "", h.lstrip("#").strip()) for h in headings]
        if len(normalized) - len(set(normalized)) >= 2:
            return True
    # 连续重复段落
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(paragraphs) >= 2:
        for index in range(len(paragraphs) - 1):
            a = paragraphs[index]
            b = paragraphs[index + 1]
            if len(a) >= 40 and a == b:
                return True
    # 同一长句出现 3 次以上
    sentences = [s.strip() for s in re.split(r"[。！？\n]", text) if len(s.strip()) >= 24]
    counts: dict[str, int] = {}
    for sentence in sentences:
        counts[sentence] = counts.get(sentence, 0) + 1
        if counts[sentence] >= 3:
            return True
    return False


def resolve_published_report(
    *,
    candidate: str | None,
    fallback_markdown: str,
    output_language: str = "zh-CN",
    decision_task: bool = True,
    repaired_candidate: str | None = None,
) -> PublicationResolution:
    """发布决议：首次门禁 → 可选一次修复 → fallback。."""
    if not candidate or not candidate.strip():
        return PublicationResolution(
            markdown=fallback_markdown,
            partial=True,
            error_codes=("missing_candidate_report",),
            used_fallback=True,
            repair_attempted=False,
        )

    first = evaluate_report_publication_gate(
        candidate,
        output_language=output_language,
        decision_task=decision_task,
    )
    if first.ok:
        return PublicationResolution(
            markdown=candidate,
            partial=False,
            error_codes=first.error_codes,
            used_fallback=False,
            repair_attempted=False,
        )

    if repaired_candidate and repaired_candidate.strip():
        second = evaluate_report_publication_gate(
            repaired_candidate,
            output_language=output_language,
            decision_task=decision_task,
        )
        if second.ok:
            return PublicationResolution(
                markdown=repaired_candidate,
                partial=False,
                error_codes=second.error_codes,
                used_fallback=False,
                repair_attempted=True,
            )
        return PublicationResolution(
            markdown=fallback_markdown,
            partial=True,
            error_codes=tuple(dict.fromkeys([*first.error_codes, *second.error_codes])),
            used_fallback=True,
            repair_attempted=True,
        )

    return PublicationResolution(
        markdown=fallback_markdown,
        partial=True,
        error_codes=first.error_codes,
        used_fallback=True,
        repair_attempted=False,
    )
