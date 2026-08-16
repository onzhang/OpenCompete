"""通用 Analysis Pack、范围冻结与证据链硬门禁。."""

from __future__ import annotations

from datetime import UTC, datetime

from opencompete.models import (
    AnalysisLevel,
    ClaimStatus,
    GateViolation,
    ResearchRequest,
    RunBundle,
    ScopeConfirmation,
    ScopeSpec,
)

# 这组键只服务于历史 ``ResearchRequest``、CLI 与固定 Benchmark 的兼容契约。
# 它们不是 Web 研究的建议范围：Web 必须先经 ScopePlanner 产出本次委托的
# 中文自然语言维度，并在传入本函数前完整写入 ``request.dimensions``。
LEGACY_FIXED_DIMENSIONS: dict[AnalysisLevel, tuple[str, ...]] = {
    AnalysisLevel.PRODUCT_SERVICE: (
        "capabilities",
        "pricing",
        "user_experience",
        "technology_integrations",
        "service_support",
    ),
    AnalysisLevel.COMPANY_ORGANIZATION: (
        "positioning_strategy",
        "business_model",
        "go_to_market",
        "operations_capabilities",
        "partnerships_ecosystem",
    ),
    AnalysisLevel.INDUSTRY_MARKET: (
        "market_size_growth",
        "customer_segments_demand",
        "competitive_landscape",
        "regulation_standards",
        "trends_risks",
    ),
}
# 旧公开名称保留，避免历史 Benchmark / 离线测试与第三方调用方断裂。
GENERAL_ANALYSIS_DIMENSIONS = LEGACY_FIXED_DIMENSIONS


def _unique(items: tuple[str, ...]) -> tuple[str, ...]:
    """保留输入顺序并去除完全相同（去空白后的）重复项。."""
    return tuple(
        dict.fromkeys(item.strip() for item in items if item.strip())
    )


def build_draft_scope(request: ResearchRequest) -> ScopeSpec:
    """为旧 ``ResearchRequest`` 构建带稳定兼容键的草案范围。.

    Web 委托由 ``ScopePlanner`` 直接构建动态 ``ScopeSpec``，绝不能经过
    此处的 legacy 补全逻辑。该函数保留给 CLI、固定 Benchmark 与既有调用方。
    """
    dimensions: dict[AnalysisLevel, tuple[str, ...]] = {}
    for level in request.levels:
        dimensions[level] = _unique(
            (*LEGACY_FIXED_DIMENSIONS[level], *request.dimensions.get(level, ()))
        )

    return ScopeSpec(
        entities=request.entities,
        levels=request.levels,
        dimensions=dimensions,
        exclusions=request.exclusions,
        revision=0,
        frozen=False,
    )


def freeze_scope(scope: ScopeSpec, confirmation: ScopeConfirmation) -> ScopeSpec:
    """校验确认版本并返回不可变的新 Scope revision。."""
    if scope.frozen:
        raise ValueError("已经冻结的范围不能再次静默修改")
    if confirmation.expected_revision != scope.revision:
        raise ValueError("确认的 revision 已过期，请重新展示范围")
    if not confirmation.accepted:
        raise ValueError("用户没有接受研究范围")

    # ``None`` 表示用户没有编辑该字段；空集合是明确提交的内容，
    # 应由 ScopeSpec 给出可见校验错误，不能用真值回退偷偷补回范围。
    entities = scope.entities if confirmation.entities is None else confirmation.entities
    levels = scope.levels if confirmation.levels is None else confirmation.levels
    requested_dimensions = (
        confirmation.dimensions
        if confirmation.dimensions is not None
        else scope.dimensions
    )
    dimensions: dict[AnalysisLevel, tuple[str, ...]] = {}
    for level in levels:
        dimensions[level] = _unique(requested_dimensions.get(level, ()))

    return ScopeSpec(
        entities=entities,
        levels=levels,
        dimensions=dimensions,
        exclusions=(
            scope.exclusions
            if confirmation.exclusions is None
            else confirmation.exclusions
        ),
        confirmed_at=confirmation.confirmed_at or datetime.now(UTC),
        revision=scope.revision + 1,
        frozen=True,
    )


def validate_traceability(bundle: RunBundle) -> list[GateViolation]:
    """确定性检查 Source 到 Report 的全部显式引用。."""
    violations: list[GateViolation] = []
    source_ids = {source.source_id for source in bundle.sources}
    evidence_by_id = {item.evidence_id: item for item in bundle.evidence}
    claim_by_id = {item.claim_id: item for item in bundle.claims}
    comparison_ids = {item.comparison_id for item in bundle.comparisons}
    report_claim_ids = set(bundle.report.claim_ids)
    report_comparison_ids = set(bundle.report.comparison_ids)
    scope_entity_ids = {entity.entity_id for entity in bundle.scope.entities}

    _append_duplicate_violations(
        violations,
        "source_id_duplicate",
        [source.source_id for source in bundle.sources],
        "sources",
    )
    _append_duplicate_violations(
        violations,
        "evidence_id_duplicate",
        [item.evidence_id for item in bundle.evidence],
        "evidence",
    )
    _append_duplicate_violations(
        violations,
        "claim_id_duplicate",
        [item.claim_id for item in bundle.claims],
        "claims",
    )

    for index, evidence in enumerate(bundle.evidence):
        if evidence.source_id not in source_ids:
            violations.append(
                GateViolation(
                    code="evidence_source_missing",
                    message="证据引用了不存在的来源",
                    path=("evidence", index, "source_id"),
                )
            )

    for index, claim in enumerate(bundle.claims):
        if claim.entity_id not in scope_entity_ids:
            violations.append(
                GateViolation(
                    code="claim_entity_out_of_scope",
                    message="结论引用了冻结范围外的对象",
                    path=("claims", index, "entity_id"),
                )
            )
        allowed_dimensions = bundle.scope.dimensions.get(claim.level, ())
        if claim.level not in bundle.scope.levels or claim.dimension not in allowed_dimensions:
            violations.append(
                GateViolation(
                    code="claim_dimension_out_of_scope",
                    message="结论使用了冻结范围外的层级或维度",
                    path=("claims", index, "dimension"),
                )
            )
        if claim.status is not ClaimStatus.UNKNOWN and not claim.evidence_ids:
            violations.append(
                GateViolation(
                    code="known_claim_without_evidence",
                    message="非 Unknown 结论没有引用证据",
                    path=("claims", index, "evidence_ids"),
                )
            )
        for evidence_id in claim.evidence_ids:
            if evidence_id not in evidence_by_id:
                violations.append(
                    GateViolation(
                        code="claim_evidence_missing",
                        message="结论引用了不存在的证据",
                        path=("claims", index, "evidence_ids"),
                    )
                )

    for index, comparison in enumerate(bundle.comparisons):
        allowed_dimensions = bundle.scope.dimensions.get(comparison.level, ())
        if (
            comparison.level not in bundle.scope.levels
            or comparison.dimension not in allowed_dimensions
        ):
            violations.append(
                GateViolation(
                    code="comparison_dimension_out_of_scope",
                    message="比较使用了冻结范围外的层级或维度",
                    path=("comparisons", index, "dimension"),
                )
            )
        for entity_id in comparison.entity_ids:
            if entity_id not in scope_entity_ids:
                violations.append(
                    GateViolation(
                        code="comparison_entity_out_of_scope",
                        message="比较包含冻结范围外的对象",
                        path=("comparisons", index, "entity_ids"),
                    )
                )
        for claim_id in comparison.claim_ids:
            referenced_claim = claim_by_id.get(claim_id)
            if referenced_claim is None:
                violations.append(
                    GateViolation(
                        code="comparison_claim_missing",
                        message="比较引用了不存在的结论",
                        path=("comparisons", index, "claim_ids"),
                    )
                )
                continue
            if referenced_claim.entity_id not in comparison.entity_ids:
                violations.append(
                    GateViolation(
                        code="comparison_claim_entity_mismatch",
                        message="比较引用的结论对象不在比较对象中",
                        path=("comparisons", index, "claim_ids"),
                    )
                )
            if (
                referenced_claim.level is not comparison.level
                or referenced_claim.dimension != comparison.dimension
            ):
                violations.append(
                    GateViolation(
                        code="comparison_claim_context_mismatch",
                        message="比较引用的结论层级或维度与比较不一致",
                        path=("comparisons", index, "claim_ids"),
                    )
                )

    for comparison_id in bundle.report.comparison_ids:
        if comparison_id not in comparison_ids:
            violations.append(
                GateViolation(
                    code="report_comparison_missing",
                    message="报告引用了不存在的比较",
                    path=("report", "comparison_ids"),
                )
            )
    for claim_id in bundle.report.claim_ids:
        if claim_id not in claim_by_id:
            violations.append(
                GateViolation(
                    code="report_claim_missing",
                    message="报告引用了不存在的结论",
                    path=("report", "claim_ids"),
                )
            )
    if report_claim_ids != set(claim_by_id):
        violations.append(
            GateViolation(
                code="report_claim_coverage_incomplete",
                message="报告没有覆盖全部结构化结论",
                path=("report", "claim_ids"),
            )
        )
    if report_comparison_ids != comparison_ids:
        violations.append(
            GateViolation(
                code="report_comparison_coverage_incomplete",
                message="报告没有覆盖全部结构化比较",
                path=("report", "comparison_ids"),
            )
        )
    for source_id in bundle.report.source_ids:
        if source_id not in source_ids:
            violations.append(
                GateViolation(
                    code="report_source_missing",
                    message="报告引用了不存在的来源",
                    path=("report", "source_ids"),
                )
            )
    if bundle.report.cross_level_conclusions:
        violations.append(
            GateViolation(
                code="report_cross_level_conclusions_unverified",
                message="正式报告不能包含未验证的跨层自由文本结论",
                path=("report", "cross_level_conclusions"),
            )
        )
    internal_ids = {
        *source_ids,
        *evidence_by_id,
        *claim_by_id,
        *comparison_ids,
    }
    if any(identifier in bundle.report.markdown for identifier in internal_ids):
        violations.append(
            GateViolation(
                code="report_internal_identifier_exposed",
                message="用户报告暴露了结构化产物内部标识",
                path=("report", "markdown"),
            )
        )
    return violations


def _append_duplicate_violations(
    violations: list[GateViolation],
    code: str,
    identifiers: list[str],
    path: str,
) -> None:
    seen: set[str] = set()
    for index, identifier in enumerate(identifiers):
        if identifier in seen:
            violations.append(
                GateViolation(
                    code=code,
                    message="结构化产物包含重复标识",
                    path=(path, index),
                )
            )
        seen.add(identifier)
