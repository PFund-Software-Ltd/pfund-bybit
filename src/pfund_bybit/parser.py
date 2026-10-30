from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from pfund_blocks.entities.markets.market_base import MarketDict
    from pfund_blocks.typing import EndpointName

from pfund_blocks.entities.products.product_type import ProductType
from pfund_blocks.enums import OptionType
from pfund_blocks.parsers.parser_base import BaseParser

from pfund_bybit.adapter import BybitAdapter


class BybitParser(BaseParser):
    adapter = BybitAdapter()
    rest_api = {"get_markets": "parse_markets"}  # noqa: RUF012

    @staticmethod
    def _to_datetime(ms: str) -> datetime | None:
        # NOTE: Bybit uses "0" for no time, e.g. the deliveryTime of perpetuals
        return datetime.fromtimestamp(int(ms) / 1000, tz=UTC) if int(ms) > 0 else None

    @staticmethod
    def _to_funding_interval(minutes: int) -> timedelta | None:
        # NOTE: Bybit uses 0 for no funding, e.g. the fundingInterval of futures
        return timedelta(minutes=minutes) if minutes else None

    def _to_product_type(self, contract_type: str) -> ProductType:
        # e.g. "LinearPerpetual" -> PERPETUAL
        return self.adapter.from_source(contract_type, group="product_type", strict=True)

    def _to_asset(self, coin: str) -> str:
        # e.g. Bybit's coin -> pfund's asset, unmapped coins pass through as is
        return self.adapter.from_source(coin, group="asset")

    def _to_option_type(self, options_type: str) -> OptionType:
        # e.g. "Put" -> OptionType.PUT
        return self.adapter.from_source(options_type, group="option_type", strict=True)

    @staticmethod
    def _to_strike_price(symbol: str) -> Decimal:
        # e.g. "BTC-24SEP27-65000-P-USDT" -> 65000
        return Decimal(symbol.split("-")[2])

    def parse_response_success(self, name: EndpointName, response: dict[str, Any]) -> bool:
        return response.get("retCode") == 0

    def parse_response_error(self, name: EndpointName, response: dict[str, Any]) -> str | None:
        return response.get("retMsg") or None

    def parse_markets(self, response: dict[str, Any]) -> list[MarketDict]:
        """Parses the markets of one category (spot, linear, inverse or option) into dicts of BaseMarket's fields."""
        # fields shared by all categories
        market = {
            "symbol": ("symbol",),
            "base_asset": ("baseCoin", self._to_asset),
            "quote_asset": ("quoteCoin", self._to_asset),
            "tick_size": ("priceFilter", "tickSize", Decimal),
            "min_quantity": ("lotSizeFilter", "minOrderQty", Decimal),
            "max_quantity": ("lotSizeFilter", "maxOrderQty", Decimal),
        }
        derivative_market = market | {
            "lot_size": ("lotSizeFilter", "qtyStep", Decimal),
            "min_price": ("priceFilter", "minPrice", Decimal),
            "max_price": ("priceFilter", "maxPrice", Decimal),
            "listed_at": ("launchTime", self._to_datetime),
            "expiration": ("deliveryTime", self._to_datetime),
            "settlement_currency": ("settleCoin", self._to_asset),
        }
        future_market = derivative_market | {
            "product_type": ("contractType", self._to_product_type),
            "min_leverage": ("leverageFilter", "minLeverage", Decimal),
            "max_leverage": ("leverageFilter", "maxLeverage", Decimal),
            "leverage_step": ("leverageFilter", "leverageStep", Decimal),
            "funding_interval": ("fundingInterval", self._to_funding_interval),
        }
        markets = {
            "spot": market | {
                "product_type": ProductType.model_validate("CRYPTO"),
                "lot_size": ("lotSizeFilter", "basePrecision", Decimal),
            },
            "linear": future_market,
            "inverse": future_market,
            "option": derivative_market | {
                "product_type": ProductType.model_validate("OPTION"),
                "option_type": ("optionsType", self._to_option_type),
                "strike_price": ("symbol", self._to_strike_price),
            },
        }
        schema = {"@data": ("result", "list"), "data": markets[response["result"]["category"]]}
        return cast("list[MarketDict]", self.parse_schema(response, schema)["data"])
