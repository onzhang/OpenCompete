"""把上游研究状态物化为可追溯的 OpenCompete Bundle。."""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig

from open_deep_research.utils import (
    RESEARCH_EVIDENCE_ARTIFACT_TYPE,
    RESEARCH_EVIDENCE_ARTIFACT_VERSION,
    TAVILY_SEARCH_SNIPPET_MAX_CHARS,
    TAVILY_SEARCH_TITLE_MAX_CHARS,
)
from opencompete.errors import DataValidationError
from opencompete.extractors import ArtifactExtraction, ArtifactExtractor
from opencompete.models import (
    BudgetSnapshot,
    Claim,
    ClaimStatus,
    Comparison,
    Evidence,
    ReportArtifact,
    RunBundle,
    RunStatus,
    ScopeSpec,
    Source,
    utc_now,
)
from opencompete.reporting import render_validated_report
from opencompete.security import (
    environment_secret_values,
    redact_text,
    validate_public_http_url,
)

_SOURCE_BLOCK = re.compile(
    r"--- SOURCE\s+\d+:\s*(?P<title>.*?)\s*---\s*\n"
    r"URL:[ \t]*(?P<url>\S+)[ \t]*\r?\n"
    r"(?:QUERY:[ \t]*(?P<query>[^\r\n]*)\r?\n)?"
    r"(?:\r?\n)*"
    r"(?:SUMMARY:\s*)?(?P<summary>.*?)"
    r"(?=\n-{20,}\s*(?:\n|$)|\n--- SOURCE|\Z)",
    re.DOTALL,
)
_SUMMARY_TAG = re.compile(r"</?(?:summary|key_excerpts)>", re.IGNORECASE)
_ERROR_REPORT_PREFIXES = (
    "error generating final report",
    "error synthesizing research report",
)
_EXTRACT_HEADER = "TAVILY_EXTRACT_JSONL_V1"
_MAX_EXTRACT_EXCERPT_CHARS = 4000
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_RESEARCHER_TERMINATION_REASONS = frozenset(
    {
        "no_tool_calls",
        "research_complete",
        "research_tool_limit",
        "model_turn_limit",
        "budget_exhausted",
    }
)
_FORCED_RESEARCHER_TERMINATION_REASONS = frozenset(
    {
        "no_tool_calls",
        "research_tool_limit",
        "model_turn_limit",
        "budget_exhausted",
    }
)


def capture_sources(
    raw_notes: Sequence[str] | str,
    *,
    accessed_at: datetime | None = None,
) -> tuple[tuple[Source, ...], tuple[Evidence, ...]]:
    """只解析上游明确的 SOURCE/URL/SUMMARY 格式。."""
    observed_at = accessed_at or utc_now()
    notes = (raw_notes,) if isinstance(raw_notes, str) else raw_notes
    sources: dict[str, Source] = {}
    evidence: dict[str, Evidence] = {}
    secrets = environment_secret_values()

    for note in notes:
        for match in _SOURCE_BLOCK.finditer(str(note)):
            try:
                url = validate_public_http_url(match.group("url"))
            except ValueError:
                continue
            title = " ".join(match.group("title").split()) or url
            summary = _clean_summary(match.group("summary"))
            if not summary:
                continue
            safe_summary = redact_text(summary, secrets)
            raw_query = (match.group("query") or "").strip()
            source = Source(
                url=url,
                title=title,
                publisher=urlsplit(url).hostname,
                accessed_at=observed_at,
            )
            item = Evidence(
                source_id=source.source_id,
                excerpt=safe_summary,
                location="Tavily SUMMARY",
                observed_at=observed_at,
                quality_flags=(
                    ("excerpt_redacted",) if safe_summary != summary else ()
                ),
                query=redact_text(raw_query, secrets)[:1000] or None,
            )
            sources.setdefault(source.source_id, source)
            evidence.setdefault(item.evidence_id, item)

        for record in _tavily_extract_records(str(note)):
            if record.get("status") != "success":
                continue
            url_value = record.get("url")
            excerpt_value = record.get("excerpt")
            char_start = record.get("char_start")
            char_end = record.get("char_end")
            if (
                not isinstance(url_value, str)
                or not isinstance(excerpt_value, str)
                or not excerpt_value
                or len(excerpt_value) > _MAX_EXTRACT_EXCERPT_CHARS
                or isinstance(char_start, bool)
                or isinstance(char_end, bool)
                or not isinstance(char_start, int)
                or not isinstance(char_end, int)
                or char_start < 0
                or char_end <= char_start
            ):
                continue
            try:
                url = validate_public_http_url(url_value)
            except ValueError:
                continue
            source = Source(
                url=url,
                title=urlsplit(url).hostname or url,
                publisher=urlsplit(url).hostname,
                accessed_at=observed_at,
            )
            request_id_value = record.get("request_id")
            usage_value = record.get("usage")
            safe_excerpt = redact_text(excerpt_value, secrets)
            raw_query = record.get("query")
            safe_query = (
                redact_text(raw_query, secrets)[:1000]
                if isinstance(raw_query, str)
                else ""
            )
            quality_flags = ["tavily_extract"]
            if record.get("excerpt_redacted") is True:
                quality_flags.append("excerpt_redacted")
            if (
                safe_excerpt != excerpt_value
                and "excerpt_redacted" not in quality_flags
            ):
                quality_flags.append("excerpt_redacted")
            item = Evidence(
                source_id=source.source_id,
                excerpt=safe_excerpt,
                location=f"Tavily Extract characters {char_start}:{char_end}",
                observed_at=observed_at,
                quality_flags=tuple(quality_flags),
                query=safe_query or None,
                request_id=(
                    request_id_value
                    if isinstance(request_id_value, str)
                    and _REQUEST_ID.fullmatch(request_id_value)
                    else None
                ),
                provider_usage=(
                    {
                        key: value
                        for key, value in usage_value.items()
                        if isinstance(key, str)
                        and key.strip()
                        and not isinstance(value, bool)
                        and isinstance(value, int | float)
                        and value >= 0
                    }
                    if isinstance(usage_value, Mapping)
                    else {}
                ),
            )
            sources.setdefault(source.source_id, source)
            evidence.setdefault(item.evidence_id, item)

    return tuple(sources.values()), tuple(evidence.values())


def _capture_artifact_sources(
    records: Sequence[Mapping[str, Any]],
    *,
    accessed_at: datetime | None = None,
) -> tuple[tuple[Source, ...], tuple[Evidence, ...]]:
    """只消费从父级 ConductResearch ToolMessage 恢复的结构化记录。."""
    observed_at = accessed_at or utc_now()
    sources: dict[str, Source] = {}
    evidence: dict[str, Evidence] = {}
    secrets = environment_secret_values()

    for record in records:
        if record.get("version") != 1 or record.get("status") != "success":
            continue
        url_value = record.get("url")
        if not isinstance(url_value, str):
            continue
        try:
            url = validate_public_http_url(url_value)
        except ValueError:
            continue

        record_type = record.get("type")
        if record_type == "tavily_search":
            title_value = record.get("title")
            summary_value = record.get("summary")
            query_value = record.get("query")
            if (
                not isinstance(title_value, str)
                or len(title_value) > TAVILY_SEARCH_TITLE_MAX_CHARS
                or not isinstance(summary_value, str)
                or len(summary_value) > TAVILY_SEARCH_SNIPPET_MAX_CHARS
                or not isinstance(query_value, str)
                or len(query_value) > 1000
            ):
                continue
            clean_summary = _clean_summary(summary_value)
            if not clean_summary:
                continue
            title = " ".join(redact_text(title_value, secrets).split()) or url
            safe_summary = redact_text(clean_summary, secrets)
            safe_query = redact_text(query_value, secrets)
            source = Source(
                url=url,
                title=title,
                publisher=urlsplit(url).hostname,
                accessed_at=observed_at,
            )
            item = Evidence(
                source_id=source.source_id,
                excerpt=safe_summary,
                location="Tavily SUMMARY",
                observed_at=observed_at,
                quality_flags=(
                    ("excerpt_redacted",)
                    if safe_summary != clean_summary
                    else ()
                ),
                query=safe_query or None,
            )
        elif record_type == "tavily_extract":
            excerpt_value = record.get("excerpt")
            char_start = record.get("char_start")
            char_end = record.get("char_end")
            if (
                not isinstance(excerpt_value, str)
                or not excerpt_value
                or len(excerpt_value) > _MAX_EXTRACT_EXCERPT_CHARS
                or isinstance(char_start, bool)
                or isinstance(char_end, bool)
                or not isinstance(char_start, int)
                or not isinstance(char_end, int)
                or char_start < 0
                or char_end <= char_start
            ):
                continue
            request_id_value = record.get("request_id")
            usage_value = record.get("usage")
            query_value = record.get("query")
            if not isinstance(query_value, str) or len(query_value) > 1000:
                continue
            safe_excerpt = redact_text(excerpt_value, secrets)
            safe_query = redact_text(query_value, secrets)
            source = Source(
                url=url,
                title=urlsplit(url).hostname or url,
                publisher=urlsplit(url).hostname,
                accessed_at=observed_at,
            )
            quality_flags = ["tavily_extract"]
            if record.get("excerpt_redacted") is True or safe_excerpt != excerpt_value:
                quality_flags.append("excerpt_redacted")
            item = Evidence(
                source_id=source.source_id,
                excerpt=safe_excerpt,
                location=f"Tavily Extract characters {char_start}:{char_end}",
                observed_at=observed_at,
                quality_flags=tuple(quality_flags),
                query=safe_query or None,
                request_id=(
                    request_id_value
                    if isinstance(request_id_value, str)
                    and _REQUEST_ID.fullmatch(request_id_value)
                    else None
                ),
                provider_usage=(
                    {
                        key: value
                        for key, value in usage_value.items()
                        if isinstance(key, str)
                        and key.strip()
                        and not isinstance(value, bool)
                        and isinstance(value, int | float)
                        and value >= 0
                    }
                    if isinstance(usage_value, Mapping)
                    else {}
                ),
            )
        else:
            continue

        sources.setdefault(source.source_id, source)
        evidence.setdefault(item.evidence_id, item)

    return tuple(sources.values()), tuple(evidence.values())


def _supervisor_evidence_records(
    upstream_state: Mapping[str, Any],
) -> tuple[tuple[Mapping[str, Any], ...], bool]:
    """Recover parent artifacts and distinguish them from legacy checkpoints."""
    messages = upstream_state.get("supervisor_messages", ())
    if not isinstance(messages, Sequence) or isinstance(messages, str | bytes):
        return (), False
    records: list[Mapping[str, Any]] = []
    artifact_contract_present = False
    for message in messages:
        if isinstance(message, ToolMessage):
            name = message.name
            artifact: object = message.artifact
        elif isinstance(message, Mapping):
            name = message.get("name")
            artifact = message.get("artifact")
        else:
            continue
        if name != "ConductResearch" or not isinstance(artifact, Mapping):
            continue
        if "research_evidence_artifacts" not in artifact:
            continue
        artifact_contract_present = True
        values = artifact.get("research_evidence_artifacts")
        if not isinstance(values, Sequence) or isinstance(values, str | bytes):
            continue
        for value in values:
            if (
                not isinstance(value, Mapping)
                or value.get("type") != RESEARCH_EVIDENCE_ARTIFACT_TYPE
                or value.get("version") != RESEARCH_EVIDENCE_ARTIFACT_VERSION
            ):
                continue
            artifact_records = value.get("records")
            if not isinstance(artifact_records, Sequence) or isinstance(
                artifact_records,
                str | bytes,
            ):
                continue
            records.extend(
                record for record in artifact_records if isinstance(record, Mapping)
            )
    return tuple(records), artifact_contract_present


async def materialize_bundle(
    *,
    run_id: str,
    scope: ScopeSpec,
    upstream_state: Mapping[str, Any],
    budget: BudgetSnapshot,
    extractor: ArtifactExtractor,
    accessed_at: datetime | None = None,
    config: RunnableConfig | None = None,
    budget_snapshot_provider: Callable[[], Awaitable[BudgetSnapshot]] | None = None,
) -> RunBundle:
    """生成并确定性约束结构化业务产物。."""
    if not scope.frozen:
        raise DataValidationError("只有冻结范围才能生成正式产物")

    raw_notes = upstream_state.get("raw_notes", ())
    if not isinstance(raw_notes, str | Sequence):
        raw_notes = ()
    evidence_records, artifact_contract_present = _supervisor_evidence_records(
        upstream_state
    )
    if artifact_contract_present:
        sources, evidence = _capture_artifact_sources(
            evidence_records,
            accessed_at=accessed_at,
        )
    else:
        # Explicit compatibility path for checkpoints written before ToolMessage
        # artifacts were introduced. New runs always carry the parent artifact key,
        # including when the trusted record list is empty.
        sources, evidence = capture_sources(raw_notes, accessed_at=accessed_at)
    original_report = str(upstream_state.get("final_report") or "").strip()
    report_failed = _is_error_report(original_report)
    report_truncated = bool(original_report) and _looks_truncated(original_report)
    report_markdown = (
        "# 部分报告\n\n最终报告生成失败，已保留可验证的研究产物。"
        if report_failed or not original_report
        else original_report
    )

    extracted = await extractor.extract(
        scope=scope,
        sources=sources,
        evidence=evidence,
        report_markdown=report_markdown,
        config=config,
    )
    # Artifact Extractor 也是正式模型调用。初始快照用于进入物化节点时的
    # 保守状态，发布前必须在提取器结算后重读同一 durable ledger。
    if budget_snapshot_provider is not None:
        budget = await budget_snapshot_provider()
    claims, claim_limitations = _sanitize_claims(scope, evidence, extracted)
    claims, coverage_limitations = _ensure_entity_claim_coverage(scope, claims)
    comparisons, comparison_limitations = _sanitize_comparisons(
        scope,
        claims,
        extracted,
    )
    coverage_unclosed = bool(
        coverage_limitations
        or any(
            item.startswith("已为缺少比较覆盖的范围单元")
            for item in comparison_limitations
        )
    )
    # 结构化补格的「待核实说明」不是执行失败：只有模型真正声称已覆盖却
    # 无法定位证据，或研究被强制终止且覆盖未闭合时，才记为 partial。
    supervisor_reason = _supervisor_termination_reason(upstream_state)
    unexecuted_research_calls = _unexecuted_research_calls(upstream_state)
    researcher_reasons, unexecuted_research_tool_calls = (
        _researcher_outcomes(upstream_state)
    )
    forced_researcher_reasons = tuple(
        dict.fromkeys(
            reason
            for reason in researcher_reasons
            if reason in _FORCED_RESEARCHER_TERMINATION_REASONS
        )
    )
    forced_termination = supervisor_reason in {
        "no_tool_calls",
        "delegation_limit",
        "model_turn_limit",
        "budget_exhausted",
    }
    limitations = [
        f"提取器未验证提示：{item}" for item in extracted.limitations if item.strip()
    ]
    if report_truncated:
        limitations.append("最终报告疑似被输出上限截断，正文可能不完整")
    # 提取调用成功但零产出（常见于输出 token 上限内模型自保输出空结构）
    # 必须显式留痕，不能静默用占位补全掩盖。
    if (
        not extracted.claims
        and not extracted.comparisons
        and not extracted.execution_failed
    ):
        limitations.append("提取器未返回任何结论或比较，范围矩阵已用待核实占位补全")
    structural_degradation = bool(
        extracted.execution_failed
        or extracted.cross_level_conclusions
        or claim_limitations
        or report_truncated
        or any(
            not item.startswith("已为缺少比较覆盖的范围单元")
            for item in comparison_limitations
        )
    )
    if extracted.cross_level_conclusions:
        limitations.append("跨层未验证自由文本已丢弃")
    limitations.extend(claim_limitations)
    limitations.extend(coverage_limitations)
    limitations.extend(comparison_limitations)
    failed_research_units = _failed_research_units(upstream_state)
    if failed_research_units:
        limitations.append(
            f"有 {failed_research_units} 个研究单元失败，已保留其他成功结果"
        )
    failed_extract_urls = (
        _failed_extract_artifact_urls(evidence_records)
        if artifact_contract_present
        else _failed_extract_urls(raw_notes)
    )
    if failed_extract_urls:
        limitations.append(
            f"Tavily Extract 有 {failed_extract_urls} 个 URL 提取失败，已保留同批成功项"
        )
    if unexecuted_research_calls:
        limitations.append(
            f"有 {unexecuted_research_calls} 个 Supervisor 研究委托未执行"
        )
    if unexecuted_research_tool_calls:
        limitations.append(
            f"有 {unexecuted_research_tool_calls} 个 Researcher 研究工具未执行"
        )
    if forced_termination:
        limitations.append(
            f"Supervisor 因 {supervisor_reason} 强制终止，且覆盖未闭合"
            if coverage_unclosed
            else f"Supervisor 因 {supervisor_reason} 终止，但结构化覆盖已闭合"
        )
    for reason in forced_researcher_reasons:
        limitations.append(
            f"Researcher 因 {reason} 强制终止，且覆盖未闭合"
            if coverage_unclosed
            else f"Researcher 因 {reason} 强制终止，但结构化覆盖已闭合"
        )
    if _budget_exhausted(budget) or bool(upstream_state.get("budget_exhausted")):
        limitations.append("预算已耗尽，已停止新增外部调用")
        if not original_report:
            original_report = (
                "# 部分研究结果\n\n"
                "研究因全局预算耗尽提前停止。以下内容仅包含预算内已经完成的"
                "研究结果；未覆盖项应视为待核实。"
            ).strip()
            report_failed = False
            report_markdown = original_report
    if report_failed or not original_report:
        limitations.append("上游最终报告生成失败")
    if not sources:
        limitations.append("没有捕获到可定位来源")
    if not claims:
        limitations.append("没有生成可发布的结构化结论")
    if not comparisons:
        limitations.append("没有生成可发布的结构化比较")
    limitations = list(dict.fromkeys(limitations))

    partial = (
        structural_degradation
        or unexecuted_research_calls > 0
        or unexecuted_research_tool_calls > 0
        or (forced_termination and coverage_unclosed)
        or (bool(forced_researcher_reasons) and coverage_unclosed)
        or failed_research_units > 0
        or report_failed
        or not original_report
        or not sources
        or not claims
        or not comparisons
    )
    from opencompete.report_publication_gate import resolve_published_report

    fallback_markdown = render_validated_report(
        scope=scope,
        claims=claims,
        comparisons=comparisons,
        sources=sources,
        evidence=evidence,
        limitations=tuple(limitations),
    )
    candidate = None if report_failed or not original_report else original_report
    # 发布路径：确定性门禁；无修复 LLM 时 fail → fallback + partial
    resolution = resolve_published_report(
        candidate=candidate,
        fallback_markdown=fallback_markdown,
        output_language="zh-CN",
        decision_task=True,
        repaired_candidate=None,
    )
    published_markdown = resolution.markdown
    if resolution.partial:
        partial = True
        if resolution.used_fallback:
            limitations = list(limitations)
            limitations.append(
                "最终报告未通过发布门禁，已使用结构化保底报告："
                + "、".join(resolution.error_codes)
            )
            limitations = list(dict.fromkeys(limitations))
    report = ReportArtifact(
        markdown=published_markdown,
        claim_ids=tuple(item.claim_id for item in claims),
        comparison_ids=tuple(item.comparison_id for item in comparisons),
        cross_level_conclusions=(),
        limitations=tuple(limitations),
        source_ids=tuple(source.source_id for source in sources),
    )
    bundle = RunBundle(
        run_id=run_id,
        scope=scope,
        sources=sources,
        evidence=evidence,
        claims=claims,
        comparisons=comparisons,
        report=report,
        budget=budget,
        status=RunStatus.PARTIAL if partial else RunStatus.COMPLETED,
    )
    violations = bundle.traceability_violations()
    if violations:
        codes = ", ".join(sorted({violation.code for violation in violations}))
        raise DataValidationError(f"结构化产物未通过引用校验：{codes}")
    return bundle


def _clean_summary(value: str) -> str:
    without_tags = _SUMMARY_TAG.sub("", value)
    return "\n".join(line.rstrip() for line in without_tags.strip().splitlines())


def _tavily_extract_records(note: str) -> tuple[Mapping[str, Any], ...]:
    """只解析正式 Extract Tool 的有界 JSONL 契约。."""
    lines = note.splitlines()
    records: list[Mapping[str, Any]] = []
    in_contract = False
    for line in lines:
        if line.strip() == _EXTRACT_HEADER:
            in_contract = True
            continue
        if not in_contract:
            continue
        if line.strip() == "</untrusted_extract_results>":
            in_contract = False
            continue
        if not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except (json.JSONDecodeError, UnicodeError):
            continue
        if (
            isinstance(value, Mapping)
            and value.get("type") == "tavily_extract"
            and value.get("version") == 1
        ):
            records.append(value)
    return tuple(records)


def _failed_extract_urls(raw_notes: Sequence[str] | str) -> int:
    notes = (raw_notes,) if isinstance(raw_notes, str) else raw_notes
    failures = {
        (record.get("batch_index"), record.get("url"))
        for note in notes
        for record in _tavily_extract_records(str(note))
        if record.get("status") == "failed" and isinstance(record.get("url"), str)
    }
    return len(failures)


def _failed_extract_artifact_urls(
    records: Sequence[Mapping[str, Any]],
) -> int:
    failures = {
        (record.get("batch_index"), record.get("url"))
        for record in records
        if record.get("type") == "tavily_extract"
        and record.get("version") == 1
        and record.get("status") == "failed"
        and isinstance(record.get("url"), str)
    }
    return len(failures)


def _is_error_report(value: str) -> bool:
    normalized = value.casefold()
    return any(normalized.startswith(prefix) for prefix in _ERROR_REPORT_PREFIXES)


# 报告正文的正常终结字符：中英句读、引号/括号闭合、列表与代码块装饰。
_REPORT_TERMINAL_CHARS = set("。！？!?…;；:：）)」』】》>”\"'`*_-.|")
_REPORT_CODE_FENCE = "```"


def _looks_truncated(text: str) -> bool:
    """启发式检测报告是否被输出上限截断。.

    真实案例：输出恰好顶满 ``final_report_model_max_tokens`` 时正文停在
    句中（如「Pro 从 $」）。剥离尾部空白与 Markdown 装饰后，末字符
    既非句读/闭合符也非代码块围栏时判为疑似截断；无标点的列表项结尾
    可能少量误报，换取截断报告不被当作完整成果发布。
    """
    stripped = text.rstrip()
    if not stripped:
        return False
    if stripped.endswith(_REPORT_CODE_FENCE):
        return False
    last = stripped[-1]
    return last not in _REPORT_TERMINAL_CHARS and not last.isspace()


def _budget_exhausted(budget: BudgetSnapshot) -> bool:
    """仅把配置为正且已经归零的预算维度视为耗尽。."""
    fields = ("input_tokens", "output_tokens", "tavily_calls", "cost_usd")
    return any(
        getattr(budget.limit, field) > 0 and getattr(budget.remaining, field) <= 0
        for field in fields
    )


def _failed_research_units(upstream_state: Mapping[str, Any]) -> int:
    """识别上游失败隔离节点留下的确定性 ToolMessage。."""
    messages = upstream_state.get("supervisor_messages", ())
    if not isinstance(messages, Sequence) or isinstance(messages, str | bytes):
        return 0
    failures = 0
    for message in messages:
        content: object
        if isinstance(message, ToolMessage):
            content = message.content
        elif isinstance(message, Mapping) and message.get("type") == "tool":
            content = message.get("content", "")
        else:
            continue
        if str(content).startswith("Research unit failed ("):
            failures += 1
    return failures


def _supervisor_termination_reason(
    upstream_state: Mapping[str, Any],
) -> str | None:
    value = upstream_state.get("supervisor_termination_reason")
    return value if isinstance(value, str) and value.strip() else None


def _unexecuted_research_calls(upstream_state: Mapping[str, Any]) -> int:
    value = upstream_state.get("unexecuted_research_calls", 0)
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return max(0, value)
    return 0


def _message_researcher_outcome(message: object) -> tuple[str | None, int]:
    artifact: object = None
    if isinstance(message, ToolMessage):
        artifact = message.artifact
    elif isinstance(message, Mapping):
        artifact = message.get("artifact")
    if not isinstance(artifact, Mapping):
        return None, 0
    outcome = artifact.get("researcher_outcome")
    if not isinstance(outcome, Mapping):
        return None, 0
    reason_value = outcome.get("termination_reason")
    reason = (
        reason_value
        if isinstance(reason_value, str)
        and reason_value in _RESEARCHER_TERMINATION_REASONS
        else None
    )
    count_value = outcome.get("unexecuted_tool_calls", 0)
    count = (
        max(0, int(count_value))
        if isinstance(count_value, bool | int)
        else 0
    )
    return reason, count


def _researcher_outcomes(
    upstream_state: Mapping[str, Any],
) -> tuple[tuple[str, ...], int]:
    messages = upstream_state.get("supervisor_messages", ())
    if not isinstance(messages, Sequence) or isinstance(messages, str | bytes):
        messages = ()
    artifact_outcomes = tuple(
        _message_researcher_outcome(message) for message in messages
    )

    reason_values = upstream_state.get("researcher_termination_reasons")
    if isinstance(reason_values, Sequence) and not isinstance(
        reason_values,
        str | bytes,
    ):
        reasons = tuple(
            value
            for value in reason_values
            if isinstance(value, str) and value in _RESEARCHER_TERMINATION_REASONS
        )
    else:
        reasons = tuple(
            reason for reason, _count in artifact_outcomes if reason is not None
        )

    if "unexecuted_research_tool_calls" in upstream_state:
        count_value = upstream_state.get("unexecuted_research_tool_calls", 0)
        unexecuted = (
            max(0, int(count_value))
            if isinstance(count_value, bool | int)
            else 0
        )
    else:
        unexecuted = sum(count for _reason, count in artifact_outcomes)
    return reasons, unexecuted


def _sanitize_claims(
    scope: ScopeSpec,
    evidence: tuple[Evidence, ...],
    extracted: ArtifactExtraction,
) -> tuple[tuple[Claim, ...], list[str]]:
    entity_ids = {entity.entity_id for entity in scope.entities}
    evidence_ids = {item.evidence_id for item in evidence}
    claims: dict[str, Claim] = {}
    limitations: list[str] = []

    for claim in extracted.claims:
        allowed_dimensions = scope.dimensions.get(claim.level, ())
        if (
            claim.entity_id not in entity_ids
            or claim.level not in scope.levels
            or claim.dimension not in allowed_dimensions
        ):
            limitations.append("已丢弃冻结范围外的结构化结论")
            continue
        if any(item not in evidence_ids for item in claim.evidence_ids):
            claim = Claim(
                claim_id=claim.claim_id,
                text=claim.text,
                entity_id=claim.entity_id,
                level=claim.level,
                dimension=claim.dimension,
                status=ClaimStatus.UNKNOWN,
                uncertainty="Extractor 引用了未捕获的证据",
                important=claim.important,
            )
            limitations.append("存在无法验证证据引用的结论，已标记为 Unknown")
        claims.setdefault(claim.claim_id, claim)
    return tuple(claims.values()), limitations


def _sanitize_comparisons(
    scope: ScopeSpec,
    claims: tuple[Claim, ...],
    extracted: ArtifactExtraction,
) -> tuple[tuple[Comparison, ...], list[str]]:
    entity_ids = {entity.entity_id for entity in scope.entities}
    claim_by_id = {claim.claim_id: claim for claim in claims}
    comparisons: dict[str, Comparison] = {}
    limitations: list[str] = []

    for comparison in extracted.comparisons:
        allowed_dimensions = scope.dimensions.get(comparison.level, ())
        if (
            comparison.level not in scope.levels
            or comparison.dimension not in allowed_dimensions
            or any(item not in entity_ids for item in comparison.entity_ids)
        ):
            limitations.append("已丢弃冻结范围外的结构化比较")
            continue
        existing_claim_ids = tuple(
            claim_id for claim_id in comparison.claim_ids if claim_id in claim_by_id
        )
        if existing_claim_ids != comparison.claim_ids:
            limitations.append("比较中的无效结论引用已被移除")
        valid_claim_ids = tuple(
            claim_id
            for claim_id in existing_claim_ids
            if (
                claim_by_id[claim_id].entity_id in comparison.entity_ids
                and claim_by_id[claim_id].level is comparison.level
                and claim_by_id[claim_id].dimension == comparison.dimension
            )
        )
        if valid_claim_ids != existing_claim_ids:
            limitations.append("比较中的上下文不一致结论引用已被移除")
        sanitized = Comparison(
            entity_ids=comparison.entity_ids,
            level=comparison.level,
            dimension=comparison.dimension,
            claim_ids=valid_claim_ids,
            differences=tuple(
                dict.fromkeys(
                    item.strip() for item in comparison.differences if item.strip()
                )
            ),
            commonalities=tuple(
                dict.fromkeys(
                    item.strip() for item in comparison.commonalities if item.strip()
                )
            ),
            gaps=tuple(
                dict.fromkeys(item.strip() for item in comparison.gaps if item.strip())
            ),
        )
        comparisons.setdefault(sanitized.comparison_id, sanitized)
    scope_entity_ids = tuple(entity.entity_id for entity in scope.entities)
    for level in scope.levels:
        for dimension in scope.dimensions[level]:
            relevant_comparisons = tuple(
                comparison
                for comparison in comparisons.values()
                if comparison.level is level and comparison.dimension == dimension
            )
            covered_entities = {
                entity_id
                for comparison in relevant_comparisons
                for entity_id in comparison.entity_ids
            }
            expected_claim_ids = tuple(
                claim.claim_id
                for claim in claims
                if claim.level is level and claim.dimension == dimension
            )
            covered_claim_ids = {
                claim_id
                for comparison in relevant_comparisons
                for claim_id in comparison.claim_ids
            }
            if (
                set(scope_entity_ids) <= covered_entities
                and set(expected_claim_ids) <= covered_claim_ids
            ):
                continue
            coverage = Comparison(
                entity_ids=scope_entity_ids,
                level=level,
                dimension=dimension,
                claim_ids=expected_claim_ids,
                gaps=("该范围单元缺少可验证比较，已显式保持待核实状态",),
            )
            comparisons.setdefault(coverage.comparison_id, coverage)
            limitations.append(
                f"已为缺少比较覆盖的范围单元补充待核实说明：{level.value}/{dimension}"
            )
    return tuple(comparisons.values()), limitations


def _ensure_entity_claim_coverage(
    scope: ScopeSpec,
    claims: tuple[Claim, ...],
) -> tuple[tuple[Claim, ...], list[str]]:
    """为每个 ``entity × level × dimension`` 缺口补显式 Unknown。."""
    covered = {(claim.entity_id, claim.level, claim.dimension) for claim in claims}
    completed = list(claims)
    missing = [
        (entity, level, dimension)
        for entity in scope.entities
        for level in scope.levels
        for dimension in scope.dimensions[level]
        if (entity.entity_id, level, dimension) not in covered
    ]
    if not missing:
        return claims, []
    for entity, level, dimension in missing:
        completed.append(
            Claim(
                text=(
                    f"{entity.name} 在 {dimension} 维度暂无可验证的结构化结论，"
                    "该范围单元标记为待核实"
                ),
                entity_id=entity.entity_id,
                level=level,
                dimension=dimension,
                status=ClaimStatus.UNKNOWN,
                uncertainty=(
                    f"{entity.name} 在 {dimension} 维度：当前研究没有生成可定位证据，"
                    "该范围单元保持待核实"
                ),
            )
        )
    return tuple(completed), [
        f"已为 {len(missing)} 个缺少结论覆盖的范围单元补充待核实说明"
    ]
