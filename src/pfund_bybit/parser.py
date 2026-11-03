from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from pfund_blocks.entities.markets.market_base import MarketDict
    from pfund_blocks.typing import EndpointName, FullChannel

    from pfund_bybit.typing import DecodedMessage, RawResponse

import msgspec
from pfund_blocks.datas.bar_data import Bar
from pfund_blocks.datas.quote_data import Quote
from pfund_blocks.datas.tick_data import Tick
from pfund_blocks.entities.products.product_type import ProductType
from pfund_blocks.enums import Channel, OptionType, Side
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
    def _ms_to_ns(ms: int) -> int:
        return int(ms) * 1_000_000

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

    def _to_side(self, side: str) -> Side:
        # e.g. "Buy" -> Side.BUY
        return self.adapter.from_source(side, group="side", strict=True)

    @staticmethod
    def _to_strike_price(symbol: str) -> Decimal:
        # e.g. "BTC-24SEP27-65000-P-USDT" -> 65000
        return Decimal(symbol.split("-")[2])

    def parse_response_success(self, name: EndpointName, response: RawResponse) -> bool:
        return response.get("retCode") == 0

    def parse_response_error(self, name: EndpointName, response: RawResponse) -> str | None:
        return response.get("retMsg") or None

    def parse_message(self, channel: FullChannel, message: DecodedMessage):
        # e.g. "kline.1.BTCUSDT" -> Channel.candlestick
        channel = self.adapter.from_source(channel.split(".")[0], group="channel", strict=True)
        if channel == Channel.candlestick:
            return self.parse_candlestick(message)
        elif channel == Channel.tick:
            return self.parse_tradebook(message)
        elif channel == Channel.orderbook:
            return self.parse_orderbook(message)
        else:
            raise NotImplementedError(f"{channel=} is not supported")

    def parse_candlestick(self, message: DecodedMessage) -> list[Bar]:
        # e.g. {"topic": "kline.5.BTCUSDT", "type": "snapshot", "ts": 1672324988882, "data": [{"start": 1672324800000,
        # "end": 1672325099999, "interval": "5", "open": "16649.5", "close": "16677", "high": "16677", "low": "16608",
        # "volume": "2.081", "turnover": "34666.4005", "confirm": false, "timestamp": 1672324988882}]}
        # e.g. "kline.5.BTCUSDT" -> 5m
        resolution = self.adapter.from_source(message["topic"].split(".")[1], group="channel_resolution", strict=True)
        schema = {
            "@data": ("data",),
            "data": {
                "resolution": resolution,
                "start_ts": ("start", self._ms_to_ns),
                # NOTE: Bybit's end is inclusive (e.g. 1672325099999), Bar's end_ts is exclusive
                "end_ts": ("end", lambda ms: self._ms_to_ns(ms + 1)),
                "updated_ts": ("timestamp", self._ms_to_ns),
                "open": ("open", float),
                "high": ("high", float),
                "low": ("low", float),
                "close": ("close", float),
                "volume": ("volume", float),
                "is_closed": ("confirm",),
                "server_ts": self._ms_to_ns(message["ts"]),
            },
        }
        return msgspec.convert(self.parse_schema(message, schema)["data"], list[Bar])

    def parse_tradebook(self, message: DecodedMessage) -> list[Tick]:
        # e.g. {"topic": "publicTrade.BTCUSDT", "type": "snapshot", "ts": 1793708150597, "data": [{"T": 1793708150596,
        # "s": "BTCUSDT", "S": "Buy", "v": "0.131", "p": "77220.00", "L": "ZeroPlusTick", "i": "f4e10f65-...", "BT": false,
        # "RPI": false, "seq": 837726029132}]}
        schema = {
            "@data": ("data",),
            "data": {
                "ts": ("T", self._ms_to_ns),
                "price": ("p", float),
                "volume": ("v", float),
                "side": ("S", self._to_side),
                "server_ts": self._ms_to_ns(message["ts"]),
            },
        }
        return msgspec.convert(self.parse_schema(message, schema)["data"], list[Tick])

    # TODO:
    # (brainstorming): OrderBook in pfund-blocks, standardize OrderBookUpdate,
    # parse into OrderBook -> orderbook.to_quote() returns Quote
    def parse_orderbook(self, message: DecodedMessage) -> list[Quote]:
        raise NotImplementedError

    def parse_message_error(self, channel: FullChannel | None, message: DecodedMessage) -> str | None:
        # e.g. {"success": false, "ret_msg": "error:handler not found,topic:kline.1.BTCUSD", "op": "subscribe"}
        if message.get("success") is False:
            return message.get("ret_msg") or "unknown error"
        # e.g. option's {"success": true, "data": {"failTopics": ["kline.1.X"], "successTopics": []}, "type": "COMMAND_RESP"}
        if message.get("type") == "COMMAND_RESP" and (fail_topics := message["data"].get("failTopics")):
            return f"failed topics: {fail_topics}"
        return None

    def parse_markets(self, response: RawResponse) -> list[MarketDict]:
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
