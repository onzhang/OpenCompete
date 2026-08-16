"""OpenCompete Bench v2 的固定数据集加载与跨文件校验。."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from opencompete.evaluation.competitive_models import (
    ClaimMateriality,
    CompetitiveBenchmarkCase,
    CompetitiveBenchmarkDataset,
    CompetitiveCaseDefinition,
    CompetitiveCriteriaDocument,
    CompetitiveDatasetManifest,
    CompetitiveReferenceMode,
    CompetitiveReviewDocument,
    CompetitiveReviewStatus,
    CompetitiveSourcesDocument,
    ReferenceClaimsDocument,
    ReferenceClaimType,
)

_REQUIRED_CASE_FILES = {
    "case.json",
    "prompt.md",
    "reference.md",
    "reference_claims.json",
    "criteria.json",
    "sources.json",
    "review.json",
}
_Model = TypeVar("_Model", bound=BaseModel)


class CompetitiveDatasetValidationError(ValueError):
    """Bench v2 数据无法通过离线契约校验。."""


class CompetitiveReferenceReviewError(CompetitiveDatasetValidationError):
    """Reference 尚未完成允许级别的专家复核。."""


def load_competitive_dataset(
    path: str | Path,
    *,
    allow_authored: bool = False,
) -> CompetitiveBenchmarkDataset:
    """读取 Bench v2，默认只允许双 AI 复核完成的 Reference。."""
    manifest_path = Path(path)
    if manifest_path.is_dir():
        manifest_path = manifest_path / "dataset.json"
    root = manifest_path.parent.resolve()
    manifest = _read_model(manifest_path, CompetitiveDatasetManifest)
    _validate_prompt_hash(
        root / "AUTHOR_PROMPT.md",
        manifest.author_prompt_sha256,
        "作者",
    )
    _validate_prompt_hash(
        root / "REVIEWER_PROMPT.md",
        manifest.reviewer_prompt_sha256,
        "Reviewer",
    )

    cases: list[CompetitiveBenchmarkCase] = []
    for entry in manifest.cases:
        case_dir = _resolve_case_dir(root, entry.path)
        files = {item.name for item in case_dir.iterdir() if item.is_file()}
        if files != _REQUIRED_CASE_FILES:
            missing = sorted(_REQUIRED_CASE_FILES - files)
            extra = sorted(files - _REQUIRED_CASE_FILES)
            raise CompetitiveDatasetValidationError(
                f"v2 案例目录文件不符合契约：{entry.case_id}；"
                f"缺少={missing}，多余={extra}"
            )
        definition = _read_model(case_dir / "case.json", CompetitiveCaseDefinition)
        criteria = _read_model(
            case_dir / "criteria.json", CompetitiveCriteriaDocument
        )
        sources = _read_model(case_dir / "sources.json", CompetitiveSourcesDocument)
        claims = _read_model(
            case_dir / "reference_claims.json", ReferenceClaimsDocument
        )
        review = _read_model(case_dir / "review.json", CompetitiveReviewDocument)
        documents = (definition, criteria, sources, claims, review)
        if any(document.case_id != entry.case_id for document in documents):
            raise CompetitiveDatasetValidationError(
                f"案例索引和文件内 case_id 不一致：{entry.case_id}"
            )
        prompt = _read_markdown(case_dir / "prompt.md")
        reference = _read_markdown(case_dir / "reference.md")
        case = CompetitiveBenchmarkCase(
            case_id=definition.case_id,
            title=definition.title,
            reference_mode=definition.reference_mode,
            as_of=definition.as_of,
            decision_context=definition.decision_context,
            research_request=definition.research_request,
            expected_scope=definition.expected_scope,
            prompt=prompt,
            reference_report=reference,
            criteria=criteria,
            sources=sources.sources,
            reference_claims=claims.claims,
            review=review,
        )
        _validate_cross_file_contract(case)
        if case.review.author_agent.prompt_sha256 != manifest.author_prompt_sha256:
            raise CompetitiveDatasetValidationError(
                f"案例作者 Prompt 哈希与数据集不一致：{entry.case_id}"
            )
        if (
            case.review.status is CompetitiveReviewStatus.EXPERT_AI_DUAL_REVIEWED
            and (
                case.review.reviewer_agent is None
                or case.review.reviewer_agent.prompt_sha256
                != manifest.reviewer_prompt_sha256
            )
        ):
            raise CompetitiveDatasetValidationError(
                f"案例 Reviewer Prompt 哈希与数据集不一致：{entry.case_id}"
            )
        if entry.content_sha256 is not None:
            from opencompete.evaluation.competitive_models import (
                competitive_case_content_sha256,
            )

            if competitive_case_content_sha256(case) != entry.content_sha256:
                raise CompetitiveDatasetValidationError(
                    f"案例语义内容哈希不一致：{entry.case_id}"
                )
        cases.append(case)

    insufficient = [
        case.case_id
        for case in cases
        if case.review.status
        not in {
            CompetitiveReviewStatus.EXPERT_AI_DUAL_REVIEWED,
            CompetitiveReviewStatus.HUMAN_APPROVED,
        }
    ]
    if insufficient and not allow_authored:
        raise CompetitiveReferenceReviewError(
            "Reference 尚未完成双 AI 专家复核，默认拒绝正式评测："
            + "、".join(insufficient)
        )
    return CompetitiveBenchmarkDataset(
        dataset_id=manifest.dataset_id,
        version=manifest.version,
        language=manifest.language,
        cases=tuple(cases),
    )


def _validate_cross_file_contract(case: CompetitiveBenchmarkCase) -> None:
    source_ids = [source.source_id for source in case.sources]
    claim_ids = [claim.claim_id for claim in case.reference_claims]
    if len(source_ids) != len(set(source_ids)):
        raise CompetitiveDatasetValidationError(
            f"Benchmark 来源标识重复：{case.case_id}"
        )
    if len(claim_ids) != len(set(claim_ids)):
        raise CompetitiveDatasetValidationError(
            f"Reference Claim 标识重复：{case.case_id}"
        )
    available_sources = set(source_ids)
    available_claims = set(claim_ids)
    for source in case.sources:
        if source.as_of > case.as_of:
            raise CompetitiveDatasetValidationError(
                f"来源 as_of 晚于案例截止日：{case.case_id}/{source.source_id}"
            )
        if source.published_at is not None and source.published_at.date() > case.as_of:
            raise CompetitiveDatasetValidationError(
                f"来源发布日期晚于案例截止日：{case.case_id}/{source.source_id}"
            )
    for claim in case.reference_claims:
        used = {support.source_id for support in claim.support}
        if not used <= available_sources:
            raise CompetitiveDatasetValidationError(
                f"Reference Claim 引用了不存在的来源：{case.case_id}/{claim.claim_id}"
            )
    for criterion in case.criteria.fact:
        if not set(criterion.reference_claim_ids) <= available_claims:
            raise CompetitiveDatasetValidationError(
                f"FACT Criterion 引用了不存在的 Claim：{case.case_id}/"
                f"{criterion.criterion_id}"
            )
    facts = [
        claim
        for claim in case.reference_claims
        if claim.claim_type is ReferenceClaimType.FACT
    ]
    unknowns = [
        claim
        for claim in case.reference_claims
        if claim.claim_type is ReferenceClaimType.UNKNOWN
    ]
    if case.reference_mode is CompetitiveReferenceMode.POSITIVE:
        if len(case.reference_claims) < 15 or len(facts) < 8:
            raise CompetitiveDatasetValidationError(
                f"正向 Reference 至少需要 15 个 Claim 和 8 个 Fact：{case.case_id}"
            )
    elif case.reference_mode is CompetitiveReferenceMode.MIXED:
        if len(case.reference_claims) < 15 or len(facts) < 5:
            raise CompetitiveDatasetValidationError(
                f"混合证据 Reference 至少需要 15 个 Claim 和 5 个 Fact：{case.case_id}"
            )
    elif (
        len(case.reference_claims) < 12
        or len(facts) < 2
        or len(unknowns) < 3
    ):
        raise CompetitiveDatasetValidationError(
            f"证据稀缺挑战案至少需要 12 Claim、2 Fact 和 3 Unknown：{case.case_id}"
        )
    critical_claims = {
        claim.claim_id
        for claim in case.reference_claims
        if claim.materiality is ClaimMateriality.CRITICAL
        and claim.claim_type in {
            ReferenceClaimType.FACT,
            ReferenceClaimType.UNKNOWN,
        }
    }
    covered_critical = {
        claim_id
        for criterion in case.criteria.fact
        for claim_id in criterion.reference_claim_ids
    }
    if not critical_claims <= covered_critical:
        missing = sorted(critical_claims - covered_critical)
        raise CompetitiveDatasetValidationError(
            f"关键 Reference Claim 未被 FACT Criteria 覆盖：{case.case_id}/{missing}"
        )
    request = case.research_request
    scope = case.expected_scope
    if dict(request.dimensions) != dict(scope.dimensions):
        raise CompetitiveDatasetValidationError(
            f"固定 ResearchRequest 与 expected_scope 的维度不一致：{case.case_id}"
        )
    if tuple(request.exclusions) != tuple(scope.exclusions):
        raise CompetitiveDatasetValidationError(
            f"固定 ResearchRequest 与 expected_scope 的排除项不一致：{case.case_id}"
        )


def _resolve_case_dir(root: Path, relative_path: str) -> Path:
    candidate = Path(relative_path)
    if candidate.is_absolute():
        raise CompetitiveDatasetValidationError("案例目录必须使用数据集内相对路径")
    resolved = (root / candidate).resolve()
    if resolved == root or root not in resolved.parents:
        raise CompetitiveDatasetValidationError("案例目录不能越出数据集路径")
    if not resolved.is_dir():
        raise CompetitiveDatasetValidationError(f"案例目录不存在：{relative_path}")
    return resolved


def _read_model(path: Path, model_type: type[_Model]) -> _Model:
    try:
        payload = path.read_text(encoding="utf-8")
        return model_type.model_validate_json(payload)
    except ValidationError as exc:
        detail = str(exc.errors()[0].get("msg", "契约不匹配"))
        raise CompetitiveDatasetValidationError(
            f"数据文件校验失败：{path}；{detail}"
        ) from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CompetitiveDatasetValidationError(f"无法读取或校验数据文件：{path}") from exc


def _read_markdown(path: Path) -> str:
    try:
        content = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise CompetitiveDatasetValidationError(f"无法读取说明文件：{path}") from exc
    if not content:
        raise CompetitiveDatasetValidationError(f"说明文件不能为空：{path}")
    return content


def _validate_prompt_hash(path: Path, expected: str, role: str) -> None:
    try:
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise CompetitiveDatasetValidationError(
            f"无法读取 {role} Prompt：{path}"
        ) from exc
    if actual != expected:
        raise CompetitiveDatasetValidationError(
            f"{role} Prompt 文件哈希与 dataset.json 不一致"
        )
