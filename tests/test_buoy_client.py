"""실제 네트워크 없이 부이 HTTP client를 검증한다."""

import io
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from unittest.mock import MagicMock, Mock

import pytest

import marine_forecast.data.buoy_client as client_module
from marine_forecast.data.buoy_client import (
    MAX_RESPONSE_BYTES,
    BuoyApiClient,
    BuoyApiError,
)


START = datetime(2026, 10, 24, 7, 0, tzinfo=timezone.utc)
END = datetime(2026, 10, 24, 8, 0, tzinfo=timezone.utc)


def make_body(station_id: str = "22101") -> bytes:
    fields = [
        "202610241700",
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

    text = "\n".join(
        [
            "#START7777",
            "# 합성 응답",
            ",".join(fields),
            "#7777END",
        ]
    )

    return text.encode("euc-kr")


def mock_response(
    monkeypatch: pytest.MonkeyPatch,
    body: bytes,
) -> tuple[Mock, MagicMock]:
    response = MagicMock()
    response.status = 200
    response.read.return_value = body
    response.__enter__.return_value = response

    opener = Mock(return_value=response)
    monkeypatch.setattr(client_module, "urlopen", opener)

    return opener, response


def test_fetch_builds_kst_query_and_calls_real_parser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opener, response = mock_response(monkeypatch, make_body())

    client = BuoyApiClient("fake-key", timeout_seconds=10)
    observations = client.fetch(
        station_id="22101",
        start=START,
        end=END,
    )

    assert len(observations) == 1
    assert observations[0].observed_at == END
    assert observations[0].get_raw("WH_SIG") == "0.2"

    request = opener.call_args.args[0]
    query = parse_qs(urlsplit(request.full_url).query)

    assert query["tm1"] == ["202610241600"]
    assert query["tm2"] == ["202610241700"]
    assert query["stn"] == ["22101"]
    assert query["authKey"] == ["fake-key"]
    assert opener.call_args.kwargs["timeout"] == 10.0

    response.read.assert_called_once_with(MAX_RESPONSE_BYTES + 1)
    response.__exit__.assert_called_once()


@pytest.mark.parametrize(
    "error",
    [
        HTTPError(
            "https://example.invalid/?authKey=fake-key",
            403,
            "Forbidden",
            None,
            io.BytesIO(),
        ),
        URLError("connection failed: fake-key"),
        TimeoutError("timeout: fake-key"),
    ],
)
def test_network_errors_do_not_expose_key(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    monkeypatch.setattr(
        client_module,
        "urlopen",
        Mock(side_effect=error),
    )

    with pytest.raises(BuoyApiError) as caught:
        BuoyApiClient("fake-key").fetch(
            station_id="22101",
            start=START,
            end=END,
        )

    assert "fake-key" not in str(caught.value)


@pytest.mark.parametrize(
    "body",
    [
        b'{"result": {"status": 403}}',
        b"\xff",
    ],
)
def test_rejects_invalid_body(
    monkeypatch: pytest.MonkeyPatch,
    body: bytes,
) -> None:
    mock_response(monkeypatch, body)

    with pytest.raises(BuoyApiError):
        BuoyApiClient("fake-key").fetch(
            station_id="22101",
            start=START,
            end=END,
        )


def test_rejects_unexpected_station(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_response(monkeypatch, make_body("22102"))

    with pytest.raises(BuoyApiError, match="unexpected station"):
        BuoyApiClient("fake-key").fetch(
            station_id="22101",
            start=START,
            end=END,
        )


def test_rejects_oversized_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_response(
        monkeypatch,
        b"x" * (MAX_RESPONSE_BYTES + 1),
    )

    with pytest.raises(BuoyApiError, match="size limit"):
        BuoyApiClient("fake-key").fetch(
            station_id="22101",
            start=START,
            end=END,
        )


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (START.replace(tzinfo=None), END),
        (START.replace(second=1), END),
        (START, START + timedelta(hours=25)),
        (END, START),
    ],
)
def test_rejects_invalid_times_before_network(
    monkeypatch: pytest.MonkeyPatch,
    start: datetime,
    end: datetime,
) -> None:
    opener = Mock()
    monkeypatch.setattr(client_module, "urlopen", opener)

    with pytest.raises(ValueError):
        BuoyApiClient("fake-key").fetch(
            station_id="22101",
            start=start,
            end=end,
        )

    opener.assert_not_called()


@pytest.mark.parametrize(
    "settings",
    [
        {"api_key": ""},
        {"api_key": "key with spaces"},
        {"api_key": "fake-key", "timeout_seconds": float("nan")},
        {"api_key": "fake-key", "timeout_seconds": True},
    ],
)
def test_rejects_invalid_client_settings(settings: dict) -> None:
    with pytest.raises((TypeError, ValueError)):
        BuoyApiClient(**settings)


def test_rejects_response_outside_requested_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_response(monkeypatch, make_body())

    with pytest.raises(BuoyApiError, match="requested interval"):
        BuoyApiClient("fake-key").fetch(
            station_id="22101",
            start=START - timedelta(hours=2),
            end=START,
        )