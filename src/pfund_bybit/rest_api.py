from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Literal, cast

if TYPE_CHECKING:
    from pfund_blocks.entities.markets.market_base import MarketDict
    from pfund_blocks.typing import Reply

    from pfund_bybit.typing import RawResponse

from http import HTTPMethod
from urllib.parse import unquote

from pfund_blocks.apis.rest_api_base import BaseRestAPI
from pfund_blocks.enums.env import Environment
from pfund_blocks.typing import Endpoint

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

    async def get_markets(self, category: Literal["spot", "linear", "inverse", "option"]) -> Reply:
        """Gets all markets of the category, with reply["response"] as the list of raw responses, one per page."""
        params = {"category": category, "limit": 1000}  # max page size, so as few calls as possible
        # NOTE: Bybit returns only BTC options by default, "All" (case-sensitive) returns every base coin's
        if category == "option":
            params["baseCoin"] = "All"
        reply = await self.send(self._create_request("get_markets", params=params))
        if not reply["success"]:
            return reply
        # NOTE: response is not None, a successful reply always has a response
        responses: list[RawResponse] = [cast("RawResponse", reply["response"])]
        # NOTE: data is not None, a successful reply is always parsed by BybitParser
        markets = cast("list[MarketDict]", reply["data"])
        # NOTE: linear, inverse and option are paginated, spot is not
        while cursor := responses[-1]["result"].get("nextPageCursor"):
            # NOTE: the cursor is url-encoded (e.g. "0%2C3"), unquote it so that it is not encoded twice
            page = await self.send(self._create_request("get_markets", params=params | {"cursor": unquote(cursor)}))
            if not page["success"]:
                return page  # fail loudly instead of returning partial markets
            responses.append(cast("RawResponse", page["response"]))
            markets += cast("list[MarketDict]", page["data"])
        reply["response"] = responses
        return reply
