"""부이 API 파서의 형식 검사와 시간 변환을 검증한다."""

from datetime import datetime, timezone

import pytest

from marine_forecast.data.buoy_api import parse_buoy_response


def make_row(
    timestamp: str = "202610231700",
    station_id: str = "22101",
) -> str:
    """실제 응답과 같은 필드 수를 가진 합성 관측을 만든다."""
    fields = [
        timestamp,
        station_id,
        "313",
        "0.4",
        "1.7",
        "309",
        "0.4",
        "1.6",
        "1020.7",
        "59.0",
        "18.0",
        "21.0",
        "0.2",
        "0.2",
        "0.2",
        "3.9",
        "242",
        "00000000000000//000",
        "---------------",
        "=",
    ]

    return ",".join(fields)


def make_response(*rows: str) -> str:
    return "\n".join(
        [
            "#START7777",
            "# Synthetic response for unit tests.",
            *rows,
            "#7777END",
        ]
    )


def test_parses_station_timestamp_and_source_fields() -> None:
    observations = parse_buoy_response(
        make_response(make_row())
    )

    assert len(observations) == 1

    observation = observations[0]

    assert observation.station_id == "22101"
    assert observation.observed_at == datetime(
        2026, 10, 23, 8, 0, tzinfo=timezone.utc
    )
    assert observation.get_raw("TM") == "202610231700"
    assert observation.get_raw("WS1") == "0.4"
    assert observation.get_raw("PA") == "1020.7"
    assert observation.get_raw("WH_SIG") == "0.2"


def test_sorts_out_of_order_observations() -> None:
    observations = parse_buoy_response(
        make_response(
            make_row("202610231700"),
            make_row("202610231100"),
            make_row("202610231600"),
        )
    )

    assert [
        observation.get_raw("TM")
        for observation in observations
    ] == [
        "202610231100",
        "202610231600",
        "202610231700",
    ]


def test_rejects_duplicate_observations() -> None:
    response = make_response(make_row(), make_row())

    with pytest.raises(ValueError, match="Duplicate observation"):
        parse_buoy_response(response)


@pytest.mark.parametrize(
    "response",
    [
        "",
        '{"result": {"status": 403}}',
        "#START7777\n#7777END",
    ],
)
def test_rejects_invalid_or_empty_responses(
    response: str,
) -> None:
    with pytest.raises(ValueError):
        parse_buoy_response(response)


def test_rejects_changed_field_count() -> None:
    fields = make_row().split(",")
    del fields[3]

    with pytest.raises(ValueError, match="Unexpected API fields"):
        parse_buoy_response(
            make_response(",".join(fields))
        )


@pytest.mark.parametrize("station_id", ["", "221A1"])
def test_rejects_invalid_station_id(station_id: str) -> None:
    with pytest.raises(ValueError, match="Invalid station ID"):
        parse_buoy_response(
            make_response(make_row(station_id=station_id))
        )


@pytest.mark.parametrize(
    "timestamp",
    [
        "202613231700",
        "20261023170",
        "not-a-time00",
    ],
)
def test_rejects_invalid_timestamp(timestamp: str) -> None:
    with pytest.raises(ValueError, match="timestamp"):
        parse_buoy_response(
            make_response(make_row(timestamp=timestamp))
        )


def test_preserves_uninterpreted_numeric_and_qc_fields() -> None:
    fields = make_row().split(",")
    fields[3] = "-99.0"
    fields[17] = "uninterpreted-qc"

    observation = parse_buoy_response(
        make_response(",".join(fields))
    )[0]

    # 파서는 아직 결측 코드나 QC 값의 의미를 임의로 해석하지 않는다.
    assert observation.get_raw("WS1") == "-99.0"
    assert observation.get_raw("AQC") == "uninterpreted-qc"


def test_rejects_unknown_field_name() -> None:
    observation = parse_buoy_response(
        make_response(make_row())
    )[0]

    with pytest.raises(KeyError):
        observation.get_raw("UNKNOWN")