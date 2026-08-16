"""不调用模型或网络的 OpenCompete Bench 硬门禁。."""

from __future__ import annotations

import json
import math
import re
from typing import Protocol

from opencompete.evaluation.models import (
    EvaluationTarget,
    HardGateEvaluation,
)
from opencompete.models import (
    BudgetEstimate,
    ClaimStatus,
    GateSeverity,
    GateViolation,
    RunBundle,
    RunStatus,
    ScopeSpec,
)
from opencompete.security import validate_public_http_url

_BUDGET_FIELDS = ("input_tokens", "output_tokens", "tavily_calls", "cost_usd")
_SECRET_PATTERNS = (
    re.compile(r"(?i)authorization\s*[:=]\s*(?:bearer\s+)?\S+"),
    re.compile(r"(?i)(?:api[-_ ]?key|access[-_ ]?token|secret)\s*[:=]\s*\S+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
)


class ScopeBenchmarkCase(Protocol):
    """硬门禁只依赖案例的固定 Scope 模板。."""

    expected_scope: ScopeSpec


def evaluate_hard_gates(
    case: ScopeBenchmarkCase,
    bundle: RunBundle,
    *,
    target: EvaluationTarget | None = None,
    require_scope_cell_coverage: bool = False,
) -> HardGateEvaluation:
    """评价范围、证据、Unknown、预算、版本和秘密泄漏。."""
    violations = list(bundle.traceability_violations())
    violations.extend(
        _artifact_coverage_violations(
            bundle,
            require_scope_cell_coverage=require_scope_cell_coverage,
        )
    )
    violations.extend(_scope_violations(case, bundle))
    violations.extend(_unknown_violations(bundle))
    violations.extend(_source_url_violations(bundle))
    violations.extend(_budget_violations(bundle))
    violations.extend(_target_violations(bundle, target))
    violations.extend(_secret_violations(bundle))
    if bundle.status not in {RunStatus.COMPLETED, RunStatus.PARTIAL}:
        violations.append(
            GateViolation(
                code="target_status_not_evaluable",
                message="只有 completed 或 partial 业务产物可以作为评测目标",
                path=("status",),
            )
        )
    passed = not any(
        violation.severity is GateSeverity.ERROR for violation in violations
    )
    return HardGateEvaluation(passed=passed, violations=tuple(violations))


def _artifact_coverage_violations(
    bundle: RunBundle,
    *,
    require_scope_cell_coverage: bool,
) -> list[GateViolation]:
    violations: list[GateViolation] = []
    required_collections = (
        ("sources", bundle.sources, "target_sources_empty", "目标没有保存任何来源"),
        ("evidence", bundle.evidence, "target_evidence_empty", "目标没有保存任何证据"),
        ("claims", bundle.claims, "target_claims_empty", "目标没有保存任何结构化结论"),
        (
            "comparisons",
            bundle.comparisons,
            "target_comparisons_empty",
            "目标没有保存任何结构化比较",
        ),
    )
    for path, values, code, message in required_collections:
        if not values:
            violations.append(
                GateViolation(code=code, message=message, path=(path,))
            )

    if require_scope_cell_coverage:
        covered_cells = {
            (claim.entity_id, claim.level, claim.dimension) for claim in bundle.claims
        }
        for entity in bundle.scope.entities:
            for level in bundle.scope.levels:
                for dimension in bundle.scope.dimensions[level]:
                    if (entity.entity_id, level, dimension) in covered_cells:
                        continue
                    violations.append(
                        GateViolation(
                            code="scope_cell_without_claim_or_unknown",
                            message=(
                                "冻结范围单元没有结构化 Claim 或显式 Unknown："
                                f"{entity.name}/{level.value}/{dimension}"
                            ),
                            path=("claims",),
                        )
                    )

    report_comparison_ids = set(bundle.report.comparison_ids)
    comparison_by_claim: dict[str, set[str]] = {}
    for comparison in bundle.comparisons:
        for claim_id in comparison.claim_ids:
            comparison_by_claim.setdefault(claim_id, set()).add(
                comparison.comparison_id
            )
    evidence_by_id = {evidence.evidence_id: evidence for evidence in bundle.evidence}
    report_source_ids = set(bundle.report.source_ids)
    for index, claim in enumerate(bundle.claims):
        if not claim.important or claim.status is ClaimStatus.UNKNOWN:
            continue
        if not (
            comparison_by_claim.get(claim.claim_id, set())
            & report_comparison_ids
        ):
            violations.append(
                GateViolation(
                    code="important_claim_not_in_report",
                    message="重要结论未通过 Comparison 进入最终报告",
                    path=("claims", index, "claim_id"),
                )
            )
        supporting_sources = {
            evidence_by_id[evidence_id].source_id
            for evidence_id in claim.evidence_ids
            if evidence_id in evidence_by_id
        }
        if not supporting_sources <= report_source_ids:
            violations.append(
                GateViolation(
                    code="important_claim_source_not_in_report",
                    message="最终报告未列出重要结论使用的全部来源",
                    path=("report", "source_ids"),
                )
            )
    return violations


def _scope_violations(
    case: ScopeBenchmarkCase,
    bundle: RunBundle,
) -> list[GateViolation]:
    expected = case.expected_scope
    actual = bundle.scope
    violations: list[GateViolation] = []
    if not actual.frozen or actual.confirmed_at is None or actual.revision < 1:
        violations.append(
            GateViolation(
                code="scope_not_frozen",
                message="目标 Bundle 的研究范围尚未确认冻结",
                path=("scope",),
            )
        )
    if {item.entity_id for item in actual.entities} != {
        item.entity_id for item in expected.entities
    }:
        violations.append(
            GateViolation(
                code="scope_entities_mismatch",
                message="目标 Bundle 的研究对象与 Benchmark 固定范围不一致",
                path=("scope", "entities"),
            )
        )
    if set(actual.levels) != set(expected.levels):
        violations.append(
            GateViolation(
                code="scope_levels_mismatch",
                message="目标 Bundle 的分析层级与 Benchmark 固定范围不一致",
                path=("scope", "levels"),
            )
        )
    actual_dimensions = {
        level: set(actual.dimensions.get(level, ())) for level in actual.levels
    }
    expected_dimensions = {
        level: set(expected.dimensions.get(level, ())) for level in expected.levels
    }
    if actual_dimensions != expected_dimensions:
        violations.append(
            GateViolation(
                code="scope_dimensions_mismatch",
                message="目标 Bundle 的分析维度与 Benchmark 固定范围不一致",
                path=("scope", "dimensions"),
            )
        )
    if set(actual.exclusions) != set(expected.exclusions):
        violations.append(
            GateViolation(
                code="scope_exclusions_mismatch",
                message="目标 Bundle 的排除项与 Benchmark 固定范围不一致",
                path=("scope", "exclusions"),
            )
        )
    return violations


def _unknown_violations(bundle: RunBundle) -> list[GateViolation]:
    violations: list[GateViolation] = []
    for index, claim in enumerate(bundle.claims):
        if claim.status is not ClaimStatus.UNKNOWN:
            continue
        if claim.evidence_ids:
            violations.append(
                GateViolation(
                    code="unknown_with_evidence",
                    message="Unknown 结论不能同时声明支持证据",
                    path=("claims", index, "evidence_ids"),
                )
            )
        if not claim.uncertainty or not claim.uncertainty.strip():
            violations.append(
                GateViolation(
                    code="unknown_without_reason",
                    message="Unknown 结论必须说明证据缺口或不确定原因",
                    path=("claims", index, "uncertainty"),
                )
            )
    return violations


def _source_url_violations(bundle: RunBundle) -> list[GateViolation]:
    violations: list[GateViolation] = []
    for index, source in enumerate(bundle.sources):
        try:
            validated = validate_public_http_url(source.url)
        except ValueError:
            validated = None
        if validated != source.url:
            violations.append(
                GateViolation(
                    code="source_url_not_public_http",
                    message="来源 URL 不是规范化的公网 HTTP/HTTPS 地址",
                    path=("sources", index, "url"),
                )
            )
    return violations


def _budget_violations(bundle: RunBundle) -> list[GateViolation]:
    budget = bundle.budget
    exceeded = any(
        getattr(budget.spent, field) + getattr(budget.reserved, field)
        > getattr(budget.limit, field) + (1e-9 if field == "cost_usd" else 0)
        for field in _BUDGET_FIELDS
    )
    violations: list[GateViolation] = []
    if exceeded:
        violations.append(
            GateViolation(
                code="budget_limit_exceeded",
                message="已用与预留预算之和超过本次运行上限",
                path=("budget",),
            )
        )
    expected_remaining = _remaining(
        budget.limit,
        budget.spent,
        budget.reserved,
    )
    if any(
        not math.isclose(
            float(getattr(expected_remaining, field)),
            float(getattr(budget.remaining, field)),
            rel_tol=1e-9,
            abs_tol=1e-9,
        )
        for field in _BUDGET_FIELDS
    ):
        violations.append(
            GateViolation(
                code="budget_snapshot_inconsistent",
                message="预算 remaining 无法由 limit、spent 和 reserved 复算",
                path=("budget", "remaining"),
            )
        )
    return violations


def _remaining(
    limit: BudgetEstimate,
    spent: BudgetEstimate,
    reserved: BudgetEstimate,
) -> BudgetEstimate:
    return BudgetEstimate(
        **{
            field: max(
                0,
                getattr(limit, field)
                - getattr(spent, field)
                - getattr(reserved, field),
            )
            for field in _BUDGET_FIELDS
        }
    )


def _target_violations(
    bundle: RunBundle,
    target: EvaluationTarget | None,
) -> list[GateViolation]:
    if target is None:
        return []
    violations: list[GateViolation] = []
    if target.run_id != bundle.run_id:
        violations.append(
            GateViolation(
                code="target_run_mismatch",
                message=f"评测绑定 run_id={target.run_id}，实际为 {bundle.run_id}",
                path=("target", "run_id"),
            )
        )
    if (
        target.artifact_revision is not None
        and target.artifact_revision != bundle.artifact_revision
    ):
        violations.append(
            GateViolation(
                code="artifact_revision_mismatch",
                message=(
                    "评测绑定 artifact revision="
                    f"{target.artifact_revision}，实际为 {bundle.artifact_revision}"
                ),
                path=("target", "artifact_revision"),
            )
        )
    return violations


def _secret_violations(bundle: RunBundle) -> list[GateViolation]:
    payload = json.dumps(bundle.model_dump(mode="json"), ensure_ascii=False)
    if not any(pattern.search(payload) for pattern in _SECRET_PATTERNS):
        return []
    return [
        GateViolation(
            code="secret_leak_detected",
            message="目标业务产物疑似包含认证信息或 API Key",
            path=("bundle",),
        )
    ]
