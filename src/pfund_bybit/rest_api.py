from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Literal

if TYPE_CHECKING:
    from pfund_blocks.apis.rest_api_base import RestResult

from http import HTTPMethod
from urllib.parse import unquote

from pfund_blocks.apis.rest_api_base import BaseRestAPI, Endpoint, RestRequest
from pfund_blocks.enums.env import Environment

from pfund_bybit.parser import BybitParser


class BybitRestAPI(BaseRestAPI):
    venue: ClassVar[str] = "BYBIT"
    version: ClassVar[str | None] = "v5"
    urls = {  # noqa: RUF012
        Environment.PAPER: "https://api-testnet.bybit.com",
        Environment.LIVE: "https://api.bybit.com",
    }
    parser: ClassVar[BybitParser] = BybitParser()  # ty: ignore[invalid-mutable-override]
    public_endpoints = {  # noqa: RUF012
        # Market endpoints:
        "get_markets": Endpoint(HTTPMethod.GET, f"/{version}/market/instruments-info"),
    }

    def _is_success(self, request: RestRequest, response: Any) -> bool:
        return response.get("retCode") == 0

    def _extract_error(self, request: RestRequest, response: Any) -> str:
        return str(response.get("retMsg", ""))

    async def get_markets(self, category: Literal["spot", "linear", "inverse", "option"]) -> RestResult:
        """Gets all markets of the category, with result["response"] as the list of raw responses, one per page."""
        params = {"category": category, "limit": 1000}  # max page size, so as few calls as possible
        result = await self.send(self._create_request("get_markets", params=params))
        if not result["success"]:
            return result
        responses = [result["response"]]
        # NOTE: linear, inverse and option are paginated, spot is not
        while cursor := responses[-1]["result"].get("nextPageCursor"):
            # NOTE: the cursor is url-encoded (e.g. "0%2C3"), unquote it so that it is not encoded twice
            page = await self.send(self._create_request("get_markets", params=params | {"cursor": unquote(cursor)}))
            if not page["success"]:
                return page  # fail loudly instead of returning partial markets
            responses.append(page["response"])
            result["data"] += page["data"]
        result["response"] = responses
        return result
