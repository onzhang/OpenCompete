"""旧兼容维度在用户可读报告中的中文展示回归测试。"""

from opencompete.analysis import LEGACY_FIXED_DIMENSIONS
from opencompete.reporting import _DIMENSION_LABELS


def test_every_legacy_dimension_has_a_chinese_display_label() -> None:
    legacy = {
        dimension
        for dimensions in LEGACY_FIXED_DIMENSIONS.values()
        for dimension in dimensions
    }

    assert legacy <= _DIMENSION_LABELS.keys()
    assert all(_DIMENSION_LABELS[key].isascii() is False for key in legacy)
