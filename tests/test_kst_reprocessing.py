"""KST 재처리가 관측값과 정확한 시간 연결을 보존하는지 검증한다."""

from pathlib import Path

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from marine_forecast.dataset import (
    attach_targets,
    load_processed_data,
    select_complete_samples,
    split_by_time,
)
from marine_forecast.features import build_features


ROOT = Path(__file__).resolve().parents[1]


def test_real_kst_reprocessing_preserves_values_features_targets_and_splits() -> None:
    old_path = ROOT / "data/processed/buoy_22101_2025_utc_assumed.csv"
    new_path = ROOT / "data/processed/buoy_22101_2025_kst_to_utc.csv"
    if not all(p.is_file() and p.with_suffix(".metadata.json").is_file()
               for p in (old_path, new_path)):
        pytest.skip("재처리 전후 로컬 데이터가 모두 있어야 비교할 수 있다")

    with pytest.warns(UserWarning, match="Source timezone is not confirmed"):
        old, _ = load_processed_data(old_path)
    new, metadata = load_processed_data(new_path)
    assert metadata["source_timezone"] == "Asia/Seoul"
    assert metadata["source_timezone_status"] == "confirmed"
    assert metadata["source_timezone_confirmation_method"] == "caller_reported"
    assert metadata["source_timezone_evidence"]
    assert new["observed_at"].min() == pd.Timestamp("2024-12-31T15:00:00Z")
    assert new["observed_at"].max() == pd.Timestamp("2025-12-31T14:00:00Z")

    # 시간대 해석 이외에 수치·결측·행 순서가 바뀌지 않아야 한다.
    assert_frame_equal(old.drop(columns="observed_at"), new.drop(columns="observed_at"))
    assert (old["observed_at"] - new["observed_at"]).eq(pd.Timedelta(hours=9)).all()

    old_dataset = attach_targets(build_features(old), old)
    new_dataset = attach_targets(build_features(new), new)
    assert_frame_equal(
        old_dataset.drop(columns=["observed_at", "target_at"]),
        new_dataset.drop(columns=["observed_at", "target_at"]),
    )
    assert (new_dataset["target_at"] - new_dataset["observed_at"]).eq(
        pd.Timedelta(hours=6)
    ).all()

    old_samples, _ = select_complete_samples(old_dataset)
    new_samples, _ = select_complete_samples(new_dataset)
    old_splits, _ = split_by_time(
        old_samples, validation_start="2025-10-01T00:00:00Z",
        test_start="2025-12-01T00:00:00Z", test_end="2026-01-01T00:00:00Z",
    )
    new_splits, report = split_by_time(
        new_samples, validation_start="2025-10-01T00:00:00+09:00",
        test_start="2025-12-01T00:00:00+09:00", test_end="2026-01-01T00:00:00+09:00",
    )
    assert report["split_rows"] == {"train": 3891, "validation": 1130, "test": 728}
    for name, part in new_splits.items():
        # 시각과 경계를 함께 옮기면 각 구간에 속하는 표본은 동일하다.
        assert_frame_equal(
            old_splits[name].drop(columns=["observed_at", "target_at"]),
            part.drop(columns=["observed_at", "target_at"]),
        )
        assert part["target_at"].lt(pd.Timestamp(part.attrs["split_end"])).all()
