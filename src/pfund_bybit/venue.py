# ty: ignore[incompatible-mutable-override]
from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Literal, cast

if TYPE_CHECKING:
    from pfund_blocks.entities.products.product_base import BaseProduct
    from pfund_blocks.resolution import Resolution


from pfund_blocks.entities.products.product_base import ProductKey
from pfund_blocks.entities.products.product_type import ProductType
from pfund_blocks.entities.venues.venue_base import (
    BaseVenue,
    VenueConfig,
    VenueMetadata,
)
from pfund_blocks.enums.env import Environment

from pfund_bybit.account import BybitAccount
from pfund_bybit.adapter import BybitAdapter
from pfund_bybit.market import BybitMarket, Category
from pfund_bybit.rest_api import BybitRestAPI


class Bybit(BaseVenue[BybitAccount, BybitMarket, VenueConfig]):
    Category = Category

    metadata: ClassVar[VenueMetadata] = VenueMetadata(
        name="BYBIT",
        url="https://www.bybit.com",
        envs=[Environment.PAPER, Environment.LIVE],
        product_types=[
            ProductType.model_validate(product_type)
            for product_type in [
                "PERPETUAL",
                "INVERSE-PERPETUAL",
                "FUTURE",
                "INVERSE-FUTURE",
                "CRYPTO",
                "OPTION",
            ]
        ],
        docs_url="https://bybit-exchange.github.io/docs/v5/intro",
    )
    adapter: ClassVar[BybitAdapter] = BybitAdapter()

    Account: ClassVar[type[BybitAccount]] = BybitAccount
    Market: ClassVar[type[BybitMarket]] = BybitMarket

    @classmethod
    def get_streaming_resolutions(cls, product: BaseProduct) -> list[Resolution]:
        """Returns the resolutions of the websocket API, since it is how Bybit provides market data."""
        # NOTE: imported here, ws_api.py imports this module
        from pfund_bybit.ws_api import BybitWebSocketAPI

        return BybitWebSocketAPI.get_supported_resolutions(product)

    async def get_markets(
        self,
        category: Category
        | Literal["LINEAR", "INVERSE", "SPOT", "OPTION"]
        | None = None,
    ) -> dict[ProductKey, BybitMarket]:
        """Fetches the markets, raises if any category fails, so the markets are never partial.

        Args:
            category: e.g. "LINEAR", if None, fetches the markets of all categories.
        """
        if category is None:
            categories = list(self.Category)
        else:
            categories = [self.Category[category.upper()]]
        # NOTE: BaseRestAPI validates env at runtime, e.g. env=BACKTEST raises
        rest_api = BybitRestAPI(
            env=cast("Literal[Environment.PAPER, Environment.LIVE]", self.env)
        )
        markets: dict[ProductKey, BybitMarket] = {}
        for category in categories:
            reply = await rest_api.get_markets(category.value)
            if not reply["success"]:
                raise RuntimeError(
                    f"{self.name} get_markets(category={category}) failed: {reply['error']}"
                )
            # NOTE: data is not None, a successful reply is always parsed by BybitParser
            for market in cast("list[BybitMarket]", reply["data"]):
                key = ProductKey(
                    symbol=market.symbol,
                    product_type=ProductType.model_validate(market.product_type),
                )
                markets[key] = market
        return markets

    @classmethod
    def get_category(cls, product: BaseProduct) -> Category:
        """Gets the product's category, e.g. in '/v5/market/instruments-info?category=linear'."""
        if product.is_crypto_spot():
            return cls.Category.SPOT
        if product.is_inverse():
            return cls.Category.INVERSE
        if product.is_option():
            return cls.Category.OPTION
        return cls.Category.LINEAR
