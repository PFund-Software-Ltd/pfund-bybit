from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

import copy
import json
from pathlib import Path

import pytest

from pfund_bybit.rest_api import BybitRestAPI

RESPONSES_DIR = Path(__file__).parent / "responses" / "rest"


def load(category: str) -> dict[str, Any]:
    """Loads the saved response as a single page, i.e. without a next page."""
    payload = json.loads((RESPONSES_DIR / f"get_markets_{category}.json").read_text())
    if "nextPageCursor" in payload["result"]:
        payload["result"]["nextPageCursor"] = ""
    return payload


def paginate(payload: dict[str, Any], page_size: int) -> list[dict[str, Any]]:
    """Splits a response into pages linked by Bybit's url-encoded nextPageCursor, e.g. "0%2C1"."""
    markets = payload["result"]["list"]
    pages = []
    for start in range(0, len(markets), page_size):
        page = copy.deepcopy(payload)
        end = start + page_size
        page["result"]["list"] = markets[start:end]
        page["result"]["nextPageCursor"] = f"0%2C{end}" if end < len(markets) else ""
        pages.append(page)
    return pages


def mock_responses(mocker: MockerFixture, api: BybitRestAPI, *payloads: dict[str, Any], status_code: int = 200) -> Any:
    """Mocks the HTTP client to return the payloads in order, one per request."""
    responses = []
    for payload in payloads:
        response = mocker.Mock(
            status_code=status_code,
            is_success=200 <= status_code < 300,
            reason_phrase="OK" if status_code == 200 else "Error",
        )
        response.json.return_value = payload
        responses.append(response)
    return mocker.patch.object(api._client, "request", mocker.AsyncMock(side_effect=responses))


@pytest.mark.asyncio
class TestGetMarkets:
    @pytest.mark.parametrize("env", ["PAPER", "LIVE"])
    async def test_request(self, mocker: MockerFixture, env: str):
        api = BybitRestAPI(env)  # ty: ignore[invalid-argument-type]
        request = mock_responses(mocker, api, load("linear"))
        await api.get_markets("linear")
        kwargs = request.call_args.kwargs
        assert kwargs["method"] == "GET"
        assert kwargs["url"] == BybitRestAPI.urls[api.env] + "/v5/market/instruments-info"
        assert kwargs["params"] == {"category": "linear", "limit": 1000}

    @pytest.mark.parametrize("category", ["spot", "linear", "inverse", "option"])
    async def test_success_is_parsed(self, mocker: MockerFixture, category: Any):
        api = BybitRestAPI("LIVE")
        payload = load(category)
        mock_responses(mocker, api, payload)
        result = await api.get_markets(category)
        assert result["success"] is True
        assert result["error"] == ""
        assert result["response"] == [payload]
        assert result["data"] == BybitRestAPI.parser.parse_markets(payload)

    async def test_ret_code_error(self, mocker: MockerFixture):
        api = BybitRestAPI("LIVE")
        mock_responses(mocker, api, {"retCode": 10001, "retMsg": "params error", "result": {}})
        result = await api.get_markets("linear")
        assert result["success"] is False
        assert result["error"] == "params error"
        assert result["data"] is None

    async def test_http_error(self, mocker: MockerFixture):
        api = BybitRestAPI("LIVE")
        mock_responses(mocker, api, {"retCode": 10001, "retMsg": "params error"}, status_code=400)
        result = await api.get_markets("linear")
        assert result["success"] is False
        assert result["error"] == "params error (HTTP 400 Error)"


@pytest.mark.asyncio
class TestGetMarketsPagination:
    async def test_pages_are_merged(self, mocker: MockerFixture):
        api = BybitRestAPI("LIVE")
        payload = load("option")
        pages = paginate(payload, page_size=1)
        request = mock_responses(mocker, api, *copy.deepcopy(pages))
        result = await api.get_markets("option")
        assert result["success"] is True
        assert result["response"] == pages  # raw, one per page
        assert result["data"] == BybitRestAPI.parser.parse_markets(payload)
        assert request.call_count == len(pages) == 3

    async def test_cursor_is_unquoted(self, mocker: MockerFixture):
        api = BybitRestAPI("LIVE")
        request = mock_responses(mocker, api, *paginate(load("option"), page_size=1))
        await api.get_markets("option")
        cursors = [call.kwargs["params"].get("cursor") for call in request.call_args_list]
        assert cursors == [None, "0,1", "0,2"]

    async def test_failed_page_is_returned(self, mocker: MockerFixture):
        api = BybitRestAPI("LIVE")
        first_page = paginate(load("option"), page_size=1)[0]
        error = {"retCode": 10006, "retMsg": "Too many visits!", "result": {}}
        mock_responses(mocker, api, first_page, error)
        result = await api.get_markets("option")
        assert result["success"] is False
        assert result["error"] == "Too many visits!"
        assert result["data"] is None


class TestEndpoints:
    @pytest.mark.parametrize("name", [*BybitRestAPI.public_endpoints, *BybitRestAPI.private_endpoints])
    def test_endpoint_has_a_method(self, name: str):
        assert callable(getattr(BybitRestAPI, name, None))

    @pytest.mark.parametrize("name", list(BybitRestAPI.parser.rest_api))
    def test_parser_entry_is_an_endpoint(self, name: str):
        assert name in BybitRestAPI.public_endpoints or name in BybitRestAPI.private_endpoints

    @pytest.mark.parametrize("method_name", list(BybitRestAPI.parser.rest_api.values()))
    def test_parser_entry_has_a_parsing_method(self, method_name: str):
        assert callable(getattr(BybitRestAPI.parser, method_name, None))
