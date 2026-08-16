from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from opencompete.analysis import (
    GENERAL_ANALYSIS_DIMENSIONS,
    build_draft_scope,
    freeze_scope,
)
from opencompete.models import (
    AnalysisLevel,
    ResearchEntity,
    ResearchRequest,
    ScopeConfirmation,
)


@pytest.mark.parametrize(
    "levels",
    [
        (AnalysisLevel.PRODUCT_SERVICE,),
        (AnalysisLevel.COMPANY_ORGANIZATION,),
        (AnalysisLevel.INDUSTRY_MARKET,),
        (
            AnalysisLevel.PRODUCT_SERVICE,
            AnalysisLevel.COMPANY_ORGANIZATION,
            AnalysisLevel.INDUSTRY_MARKET,
        ),
    ],
)
def test_unknown_industry_uses_general_dimensions_for_any_level_combination(
    levels: tuple[AnalysisLevel, ...],
) -> None:
    request = ResearchRequest(
        query="比较两个此前未配置领域包的对象",
        entities=(ResearchEntity(name="对象 A"), ResearchEntity(name="对象 B")),
        levels=levels,
    )

    scope = build_draft_scope(request)

    assert scope.levels == levels
    for level in levels:
        assert set(GENERAL_ANALYSIS_DIMENSIONS[level]).issubset(scope.dimensions[level])


def test_freezing_scope_returns_an_immutable_new_revision() -> None:
    request = ResearchRequest(
        query="比较对象 A 与对象 B",
        entities=(ResearchEntity(name="对象 A"), ResearchEntity(name="对象 B")),
        levels=(AnalysisLevel.PRODUCT_SERVICE,),
    )
    draft = build_draft_scope(request)

    frozen = freeze_scope(
        draft,
        ScopeConfirmation(
            accepted=True,
            expected_revision=draft.revision,
            confirmed_at=datetime.now(UTC),
        ),
    )

    assert frozen.frozen is True
    assert frozen.revision == draft.revision + 1
    assert draft.frozen is False
    with pytest.raises(ValidationError):
        setattr(frozen, "revision", 99)
    with pytest.raises(TypeError):
        frozen.dimensions[AnalysisLevel.PRODUCT_SERVICE] = ("other",)


def test_stale_or_repeated_confirmation_is_rejected() -> None:
    draft = build_draft_scope(
        ResearchRequest(
            query="比较对象 A 与对象 B",
            entities=(ResearchEntity(name="对象 A"), ResearchEntity(name="对象 B")),
            levels=(AnalysisLevel.PRODUCT_SERVICE,),
        )
    )
    stale = ScopeConfirmation(accepted=True, expected_revision=draft.revision + 1)

    with pytest.raises(ValueError, match="revision"):
        freeze_scope(draft, stale)

    frozen = freeze_scope(
        draft,
        ScopeConfirmation(accepted=True, expected_revision=draft.revision),
    )
    with pytest.raises(ValueError, match="冻结"):
        freeze_scope(
            frozen,
            ScopeConfirmation(accepted=True, expected_revision=frozen.revision),
        )


def test_freeze_preserves_user_edited_dynamic_dimensions_without_fallback() -> None:
    draft = build_draft_scope(
        ResearchRequest(
            query="调研 Trae 的 AI IDE 竞品",
            entities=(ResearchEntity(name="Trae"),),
            levels=(AnalysisLevel.PRODUCT_SERVICE,),
            dimensions={
                AnalysisLevel.PRODUCT_SERVICE: (
                    "代码库上下文连续性",
                    "Agent 编程工作流",
                )
            },
        )
    )

    frozen = freeze_scope(
        draft,
        ScopeConfirmation(
            accepted=True,
            expected_revision=0,
            dimensions={
                AnalysisLevel.PRODUCT_SERVICE: ("个人版配额与使用成本",)
            },
            exclusions=("未公开路线图",),
        ),
    )

    assert frozen.dimensions == {
        AnalysisLevel.PRODUCT_SERVICE: ("个人版配额与使用成本",)
    }
    assert frozen.exclusions == ("未公开路线图",)


def test_explicitly_empty_dimension_list_is_rejected_instead_of_reinjected() -> None:
    draft = build_draft_scope(
        ResearchRequest(
            query="调研 Trae 的 AI IDE 竞品",
            entities=(ResearchEntity(name="Trae"),),
            levels=(AnalysisLevel.PRODUCT_SERVICE,),
            dimensions={
                AnalysisLevel.PRODUCT_SERVICE: ("代码库上下文连续性",)
            },
        )
    )

    with pytest.raises(ValidationError, match="至少需要一个维度"):
        freeze_scope(
            draft,
            ScopeConfirmation(
                accepted=True,
                expected_revision=0,
                dimensions={AnalysisLevel.PRODUCT_SERVICE: ()},
            ),
        )
