"""예측 입력과 정답의 이름 및 순서를 정의한다."""

import pandas as pd


# 모든 학습·평가 코드가 같은 예측 간격을 사용하게 한다.
HORIZON_HOURS: int = 6

# 행 개수가 아니라 실제 시간 차이로 연결할 과거 관측 간격이다.
LAG_HOURS: tuple[int, ...] = (1, 3, 6)

# 원본에서 받아야 할 열
REQUIRED_OBSERVATION_COLUMNS: tuple[str, ...] = (
    "station_id",
    "observed_at",
    "significant_wave_height_m",
    "wind_speed_mps",
    "station_pressure_hpa",
)

# 과거값 계산까지 끝난 뒤 모델에 넣을 열
FEATURE_COLUMNS: tuple[str, ...] = (
    "significant_wave_height_m",
    "wind_speed_mps",
    "station_pressure_hpa",
    "wave_height_lag_1h_m",
    "wave_height_lag_3h_m",
    "wave_height_lag_6h_m",
    "wave_height_change_1h_m",
)

# 미래 정답은 입력 특성과 분리해 데이터 누수를 방지한다.
TARGET_COLUMN: str = "target_significant_wave_height_6h_m"

# 입력: C2 에서 읽은 관측 DataFrame
# 출력: 식별 정보 2열과 입력 특성 7열을 가진 새 DataFrame
def build_features(observations: pd.DataFrame) -> pd.DataFrame:
    """현재와 정확한 과거 시각의 관측값으로 예측 입력을 만든다"""

    missing_columns = set(
        REQUIRED_OBSERVATION_COLUMNS
    ).difference(observations.columns)

    if missing_columns:
        raise ValueError(
            f"Missing observation columns: {sorted(missing_columns)}"
        )

    if observations.empty:
        raise ValueError("No observations to build features from.")

    keys = ["station_id", "observed_at"]

    # 연결 키가 비어 있으면 과거 관측과 정확히 연결할 수 없다
    if observations[keys].isna().any().any():
        raise ValueError("Observation keys must not be missing.")

    if not isinstance(observations["station_id"].dtype, pd.StringDtype):
        raise TypeError("station_id must use pandas string dtype.")

    if observations["station_id"].str.strip().eq("").any():
        raise ValueError("station_id must not be empty.")

    time_dtype = observations["observed_at"].dtype

    # 시간 차이를 계산하기 전에 시간대가 포함된 날짜형인지 확인해야한다
    if not isinstance(time_dtype, pd.DatetimeTZDtype):
        raise TypeError("observed_at must be timezone-aware datetime.")

    if str(time_dtype.tz) != "UTC":
        raise ValueError("observed_at must use UTC.")

    if observations.duplicated(keys).any():
        raise ValueError("Duplicate station_id and observed_at keys.")

    for column in REQUIRED_OBSERVATION_COLUMNS:
        if column in keys:
            continue

        # 숫자 문자열인 상태로 계산을 시작하지 않는다
        if not pd.api.types.is_numeric_dtype(observations[column].dtype):
            raise TypeError(f"{column} must have a numeric dtype.")

        if observations[column].isin(
            [float("inf"), float("-inf")]
        ).any():
            raise ValueError(f"{column} must not contain infinity.")

    # 필요한 열만 복사해 원붠 관측 DF 을 변경하지 않는다
    result = observations.loc[
        :, list(REQUIRED_OBSERVATION_COLUMNS)
    ].copy()

    history = observations.loc[
        :, keys + ["significant_wave_height_m"]
    ].copy()

    for hours in LAG_HOURS:
        lag_column = f"wave_height_lag_{hours}h_m"

        shifted = history.rename(
            columns={"significant_wave_height_m": lag_column}
        )


        # 과거 관측을 그 값을 참조할 기준시각에 대응시킨다.
        # 예를 들어 08시 관측에 1시간을 더해 09시 행과 연결한다.
        shifted["observed_at"] = (
            # 인자로 입력받은 hours 만큼 움직인다 
            # 과거 표의 연결용 시각만 ~시간 앞으로 옮겨서 두키를 같게 만든다
            shifted["observed_at"] + pd.Timedelta(hours=hours)
        )

        result = result.merge(
            shifted,
            on=keys, # 관측소와 시각 두값이 모두 일치해야 연결
            how="left",
            validate="one_to_one",
            sort=False,
        )

    # 어느 한쪽 관측이 없으면 변화량도 결측으로 남긴다.
    result["wave_height_change_1h_m"] = (
        result["significant_wave_height_m"]
        - result["wave_height_lag_1h_m"]
    )

    # 모델 입력 순서를 고정하고 관측소·시각순으로 정렬한다.
    result = (
        result.loc[:, keys + list(FEATURE_COLUMNS)]
        .sort_values(keys)
        .reset_index(drop=True)
    )

    # 연결 과정에서 사라질 수 있는 시간대 확인 상태 등을 이어받는다.
    result.attrs = observations.attrs.copy()

    return result
