"""OpenCompete Bench v1 固定八案例目录契约测试。"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from pathlib import Path

from opencompete.analysis import (
    GENERAL_ANALYSIS_DIMENSIONS,
    build_draft_scope,
    freeze_scope,
)
from opencompete.evaluation.dataset import load_dataset
from opencompete.models import ResearchRequest, ScopeConfirmation

ROOT = Path("benchmarks/opencompete/v1")
EXPECTED_CASES = {
    "ocb-001-aws-lambda-vs-azure-functions",
    "ocb-002-m365-vs-google-workspace",
    "ocb-003-visa-vs-mastercard",
    "ocb-004-ups-vs-fedex",
    "ocb-005-kindle-vs-kobo",
    "ocb-006-spotify-vs-apple-music",
    "ocb-007-supercharger-vs-ionity",
    "ocb-008-cloud-infrastructure-competition",
}
REQUIRED_FILES = {
    "case.json",
    "prompt.md",
    "reference.md",
    "criteria.json",
    "sources.json",
}


def test_v1_contains_exactly_the_fixed_eight_case_directories() -> None:
    case_dirs = {path.name for path in (ROOT / "cases").iterdir() if path.is_dir()}

    assert case_dirs == EXPECTED_CASES
    for case_id in EXPECTED_CASES:
        files = {path.name for path in (ROOT / "cases" / case_id).iterdir() if path.is_file()}
        assert files == REQUIRED_FILES


def test_every_case_is_honestly_marked_unreviewed_and_has_authoritative_sources() -> None:
    dataset = load_dataset(ROOT, allow_draft=True)
    chinese = re.compile(r"[\u4e00-\u9fff]")

    for case in dataset.cases:
        assert case.reference_review.status.value == "draft"
        assert case.reference_review.reviewed_by is None
        assert case.reference_review.reviewed_at is None
        assert "未审核" in case.reference_review.notes
        assert chinese.search(case.prompt)
        assert chinese.search(case.reference_report)
        assert len(case.sources) >= 2
        assert all(source.as_of <= case.as_of for source in case.sources)
        assert all(source.authority.value != "other" for source in case.sources)
        source_ids = {source.source_id for source in case.sources}
        assert all(
            set(criterion.source_ids) <= source_ids
            for criterion in case.criteria.fact
        )


def test_reference_access_dates_do_not_exceed_case_cutoff() -> None:
    dataset = load_dataset(ROOT, allow_draft=True)
    access_date = re.compile(
        r"(\d{4}-\d{2}-\d{2})[^\n|]{0,20}(?:复核|访问结果)"
    )

    for case in dataset.cases:
        referenced_dates = {
            date.fromisoformat(value)
            for value in access_date.findall(case.reference_report)
        }
        assert all(value <= case.as_of for value in referenced_dates), case.case_id


def test_reference_reports_only_use_declared_source_ids() -> None:
    dataset = load_dataset(ROOT, allow_draft=True)
    source_citation = re.compile(r"\[([a-z0-9]+(?:-[a-z0-9]+)+)\]")

    for case in dataset.cases:
        declared = {source.source_id for source in case.sources}
        cited = set(source_citation.findall(case.reference_report))
        assert cited == declared, case.case_id


def test_every_case_scope_is_constructible_by_production_scope_rules() -> None:
    dataset = load_dataset(ROOT, allow_draft=True)
    confirmed_at = datetime(2026, 7, 15, tzinfo=timezone.utc)

    for case in dataset.cases:
        task_dimensions = {}
        for level in case.expected_scope.levels:
            required = GENERAL_ANALYSIS_DIMENSIONS[level]
            expected = case.expected_scope.dimensions[level]
            assert expected[: len(required)] == required
            task_dimensions[level] = expected[len(required) :]
            assert task_dimensions[level]
            assert all(f"`{dimension}`" in case.prompt for dimension in required)
            assert all(dimension in case.prompt for dimension in task_dimensions[level])

        assert "通用必选维度" in case.prompt
        assert "额外重点覆盖" in case.prompt
        request = ResearchRequest(
            query=case.prompt,
            entities=case.expected_scope.entities,
            levels=case.expected_scope.levels,
            dimensions=task_dimensions,
            exclusions=case.expected_scope.exclusions,
        )

        draft = build_draft_scope(request)
        assert draft.model_dump() == case.expected_scope.model_dump()

        frozen = freeze_scope(
            draft,
            ScopeConfirmation(
                accepted=True,
                expected_revision=draft.revision,
                confirmed_at=confirmed_at,
            ),
        )
        assert frozen.entities == case.expected_scope.entities
        assert frozen.levels == case.expected_scope.levels
        assert dict(frozen.dimensions) == dict(case.expected_scope.dimensions)
        assert frozen.exclusions == case.expected_scope.exclusions
        assert frozen.confirmed_at == confirmed_at
        assert frozen.revision == 1
        assert frozen.frozen is True
