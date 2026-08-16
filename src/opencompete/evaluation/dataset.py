"""固定 OpenCompete Bench 数据集和目标绑定加载器。."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from opencompete.evaluation.models import (
    BenchmarkCase,
    BenchmarkCriteria,
    BenchmarkDataset,
    CaseDefinition,
    DatasetManifest,
    EvaluationTarget,
    ReviewStatus,
    SourcesDocument,
    TargetsDocument,
)

_REQUIRED_CASE_FILES = {
    "case.json",
    "prompt.md",
    "reference.md",
    "criteria.json",
    "sources.json",
}
_Model = TypeVar("_Model", bound=BaseModel)


class DatasetValidationError(ValueError):
    """固定数据集无法通过离线契约校验。."""


class DraftReferenceError(DatasetValidationError):
    """评测试图使用尚未完成人工审核的参考报告。."""


def load_dataset(
    path: str | Path,
    *,
    allow_draft: bool = False,
) -> BenchmarkDataset:
    """读取固定案例，默认拒绝未经过人工审核的参考报告。."""
    manifest_path = Path(path)
    if manifest_path.is_dir():
        manifest_path = manifest_path / "dataset.json"
    root = manifest_path.parent.resolve()
    manifest = _read_model(manifest_path, DatasetManifest)

    cases: list[BenchmarkCase] = []
    for entry in manifest.cases:
        case_dir = _resolve_case_dir(root, entry.path)
        files = {item.name for item in case_dir.iterdir() if item.is_file()}
        if files != _REQUIRED_CASE_FILES:
            missing = sorted(_REQUIRED_CASE_FILES - files)
            extra = sorted(files - _REQUIRED_CASE_FILES)
            raise DatasetValidationError(
                f"案例目录文件不符合契约：{entry.case_id}；"
                f"缺少={missing}，多余={extra}"
            )
        definition = _read_model(case_dir / "case.json", CaseDefinition)
        criteria = _read_model(case_dir / "criteria.json", BenchmarkCriteria)
        sources = _read_model(case_dir / "sources.json", SourcesDocument)
        if definition.case_id != entry.case_id or sources.case_id != entry.case_id:
            raise DatasetValidationError(
                f"案例索引和文件内 case_id 不一致：{entry.case_id}"
            )
        prompt = _read_markdown(case_dir / "prompt.md")
        reference = _read_markdown(case_dir / "reference.md")
        try:
            case = BenchmarkCase(
                case_id=definition.case_id,
                title=definition.title,
                as_of=definition.as_of,
                expected_scope=definition.expected_scope,
                prompt=prompt,
                reference_report=reference,
                reference_review=definition.reference_review,
                criteria=criteria,
                sources=sources.sources,
            )
        except ValidationError as exc:
            raise DatasetValidationError(
                f"案例跨文件契约校验失败：{entry.case_id}"
            ) from exc
        cases.append(case)

    drafts = [
        case.case_id
        for case in cases
        if case.reference_review.status is ReviewStatus.DRAFT
    ]
    if drafts and not allow_draft:
        joined = "、".join(drafts)
        raise DraftReferenceError(
            f"参考报告尚未完成人工审核，默认拒绝评测：{joined}"
        )
    try:
        return BenchmarkDataset(
            dataset_id=manifest.dataset_id,
            version=manifest.version,
            language=manifest.language,
            cases=tuple(cases),
        )
    except ValidationError as exc:
        raise DatasetValidationError("数据集聚合契约校验失败") from exc


def load_targets(path: str | Path) -> tuple[EvaluationTarget, ...]:
    """读取 CLI 批量评测使用的 targets.json。."""
    document = _read_model(Path(path), TargetsDocument)
    return document.targets


def _resolve_case_dir(root: Path, relative_path: str) -> Path:
    candidate = Path(relative_path)
    if candidate.is_absolute():
        raise DatasetValidationError("案例目录必须使用数据集内相对路径")
    resolved = (root / candidate).resolve()
    if resolved == root or root not in resolved.parents:
        raise DatasetValidationError("案例目录不能越出数据集路径")
    if not resolved.is_dir():
        raise DatasetValidationError(f"案例目录不存在：{relative_path}")
    return resolved


def _read_model(path: Path, model_type: type[_Model]) -> _Model:
    try:
        payload = path.read_text(encoding="utf-8")
        return model_type.model_validate_json(payload)
    except ValidationError as exc:
        detail = str(exc.errors()[0].get("msg", "契约不匹配"))
        raise DatasetValidationError(
            f"数据文件校验失败：{path}；{detail}"
        ) from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DatasetValidationError(f"无法读取或校验数据文件：{path}") from exc


def _read_markdown(path: Path) -> str:
    try:
        content = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise DatasetValidationError(f"无法读取说明文件：{path}") from exc
    if not content:
        raise DatasetValidationError(f"说明文件不能为空：{path}")
    return content
