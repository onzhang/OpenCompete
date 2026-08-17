"""Bench v3 数据集加载。."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from opencompete.evaluation.competitive_models import CompetitiveReviewStatus
from opencompete.evaluation.competitive_v3_models import (
    CompetitiveV3BenchmarkCase,
    CompetitiveV3BenchmarkDataset,
    CompetitiveV3CaseDefinition,
    CompetitiveV3DatasetManifest,
    CompetitiveV3ReferenceClaimsDocument,
    CompetitiveV3ReviewDocument,
    CompetitiveV3SourcesDocument,
    RaceV3CriteriaDocument,
    competitive_v3_case_content_sha256,
    normalize_v3_task_text,
)

_REQUIRED = {
    "case.json",
    "prompt.md",
    "reference.md",
    "criteria.json",
    "reference_claims.json",
    "sources.json",
    "review.json",
}
_Model = TypeVar("_Model", bound=BaseModel)


class CompetitiveV3DatasetError(ValueError):
    """v3 数据集契约失败。."""


def load_competitive_v3_dataset(
    path: str | Path,
    *,
    allow_authored: bool = False,
) -> CompetitiveV3BenchmarkDataset:
    """加载 v3 数据集；默认拒绝未完成复核的案例。."""
    manifest_path = Path(path)
    if manifest_path.is_dir():
        manifest_path = manifest_path / "dataset.json"
    root = manifest_path.parent.resolve()
    manifest = _read_model(manifest_path, CompetitiveV3DatasetManifest)
    cases: list[CompetitiveV3BenchmarkCase] = []
    for entry in manifest.cases:
        case_dir = (root / entry.path).resolve()
        if not str(case_dir).startswith(str(root)):
            raise CompetitiveV3DatasetError(f"案例路径越界：{entry.path}")
        files = {item.name for item in case_dir.iterdir() if item.is_file()}
        if not _REQUIRED <= files:
            missing = sorted(_REQUIRED - files)
            raise CompetitiveV3DatasetError(
                f"v3 案例缺少文件：{entry.case_id}；缺少={missing}"
            )
        definition = _read_model(case_dir / "case.json", CompetitiveV3CaseDefinition)
        criteria = _read_model(case_dir / "criteria.json", RaceV3CriteriaDocument)
        claims_doc = _read_model(
            case_dir / "reference_claims.json",
            CompetitiveV3ReferenceClaimsDocument,
        )
        sources_doc = _read_model(
            case_dir / "sources.json",
            CompetitiveV3SourcesDocument,
        )
        review = _read_model(case_dir / "review.json", CompetitiveV3ReviewDocument)
        for document in (definition, criteria, claims_doc, sources_doc, review):
            if document.case_id != entry.case_id:
                raise CompetitiveV3DatasetError(
                    f"case_id 不一致：索引 {entry.case_id}"
                )
        if (
            not allow_authored
            and review.status
            not in {
                CompetitiveReviewStatus.EXPERT_AI_DUAL_REVIEWED,
                CompetitiveReviewStatus.HUMAN_APPROVED,
            }
        ):
            raise CompetitiveV3DatasetError(
                f"案例未完成允许级别的复核：{entry.case_id} status={review.status}"
            )
        prompt = _read_text(case_dir / "prompt.md")
        reference = _read_text(case_dir / "reference.md")
        task_sha = hashlib.sha256(
            normalize_v3_task_text(prompt).encode("utf-8")
        ).hexdigest()
        case = CompetitiveV3BenchmarkCase(
            case_id=definition.case_id,
            title=definition.title,
            as_of=definition.as_of,
            reference_mode=definition.reference_mode,
            research_request=definition.research_request,
            expected_scope=definition.expected_scope,
            prompt=prompt,
            reference=reference,
            criteria=criteria,
            reference_claims=claims_doc.claims,
            sources=sources_doc.sources,
            review=review,
            normalized_task_sha256=task_sha,
        )
        content_sha = competitive_v3_case_content_sha256(case)
        if content_sha != entry.content_sha256:
            raise CompetitiveV3DatasetError(
                f"案例内容哈希不匹配：{entry.case_id} "
                f"manifest={entry.content_sha256} actual={content_sha}"
            )
        cases.append(case)
    return CompetitiveV3BenchmarkDataset(
        dataset_id=manifest.dataset_id,
        version=manifest.version,
        language=manifest.language,
        cases=tuple(cases),
    )


def _read_model(path: Path, model: type[_Model]) -> _Model:
    try:
        return model.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except Exception as exc:  # noqa: BLE001 — 统一包装
        raise CompetitiveV3DatasetError(f"无法解析 {path.name}：{exc}") from exc


def _read_text(path: Path) -> str:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise CompetitiveV3DatasetError(f"空文件：{path.name}")
    return text
