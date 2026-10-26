"""최종 평가의 지표 계산을 가상 표본으로 먼저 검증한다."""

import importlib.util
from pathlib import Path

import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("final_evaluation", ROOT / "scripts/evaluate_final_test.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_summary_error_sign_and_shortfall():
    result = module.summarize(pd.Series([2., 1., 1.]), pd.Series([1., 2., 1.]))
    assert result["mae_m"] == pytest.approx(2/3)
    assert result["rmse_m"] == pytest.approx((2/3)**.5)
    assert result["bias_m"] == 0
    assert result["underprediction_count"] == 1
    assert result["underprediction_rate"] == pytest.approx(1/3)
    assert result["mean_shortfall_when_under_m"] == 1
    assert result["mean_shortfall_all_m"] == pytest.approx(1/3)


def test_empty_segment_is_not_zero_error():
    result = module.summarize(pd.Series(dtype=float), pd.Series(dtype=float))
    assert result["samples"] == 0
    assert result["mae_m"] is None
    assert result["underprediction_rate"] is None


def test_no_underprediction_is_not_zero_conditional_shortfall():
    result = module.summarize(pd.Series([1.]), pd.Series([1.]))
    assert result["underprediction_count"] == 0
    assert result["mean_shortfall_when_under_m"] is None
    assert result["mean_shortfall_all_m"] == 0


def test_index_mismatch_is_rejected():
    with pytest.raises(ValueError):
        module.summarize(pd.Series([1.], index=[0]), pd.Series([1.], index=[1]))


def test_previous_output_is_never_overwritten(tmp_path):
    with pytest.raises(FileExistsError):
        module.evaluate(tmp_path / "missing.csv", tmp_path / "missing-model", tmp_path)
