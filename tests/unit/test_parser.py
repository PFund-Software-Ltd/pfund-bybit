from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

import pytest
from msgspec import ValidationError
from pfund_blocks.entities.markets.market_base import BaseMarket
from pfund_blocks.entities.products.product_type import ProductType
from pfund_blocks.enums import OptionType, Side
from pfund_blocks.parsers.dsl import SchemaParseError
from pfund_blocks.resolution import Resolution

from pfund_bybit.parser import BybitParser

RESPONSES_DIR = Path(__file__).parent / "responses" / "rest"
MESSAGES_DIR = Path(__file__).parent / "responses" / "ws"
CATEGORIES = ["spot", "linear", "inverse", "option"]


def load(category: str) -> dict[str, Any]:
    return json.loads((RESPONSES_DIR / f"get_markets_{category}.json").read_text())


def load_message(name: str) -> dict[str, Any]:
    return json.loads((MESSAGES_DIR / f"{name}.json").read_text())


def parse_markets(category: str) -> list[dict[str, Any]]:
    return BybitParser().parse_response("get_markets", load(category))


class TestParseMarkets:
    def test_spot(self):
        assert parse_markets("spot")[0] == {
            "symbol": "BTCUSDT",
            "base_asset": "BTC",
            "quote_asset": "USDT",
            "product_type": ProductType.model_validate("CRYPTO"),
            "tick_size": Decimal("0.1"),
            "lot_size": Decimal("0.000001"),
            "min_quantity": Decimal("0.000001"),
            "max_quantity": Decimal(230),
        }

    def test_linear_perpetual(self):
        assert parse_markets("linear")[0] == {
            "symbol": "BTCUSDT",
            "base_asset": "BTC",
            "quote_asset": "USDT",
            "product_type": ProductType.model_validate("PERPETUAL"),
            "tick_size": Decimal("0.10"),
            "lot_size": Decimal("0.001"),
            "min_price": Decimal("0.10"),
            "max_price": Decimal("1999999.80"),
            "min_quantity": Decimal("0.001"),
            "max_quantity": Decimal("1500.000"),
            "min_leverage": Decimal(1),
            "max_leverage": Decimal("150.00"),
            "leverage_step": Decimal("0.01"),
            "listed_at": datetime(2020, 3, 15, tzinfo=UTC),
            "expiration": None,
            "settlement_currency": "USDT",
            "funding_interval": timedelta(hours=8),
        }

    def test_inverse_perpetual(self):
        market = parse_markets("inverse")[0]
        assert market["symbol"] == "BTCUSD"
        assert market["product_type"] == ProductType.model_validate("INVERSE-PERPETUAL")
        assert market["settlement_currency"] == "BTC"
        assert market["expiration"] is None
        assert market["funding_interval"] == timedelta(hours=8)

    @pytest.mark.parametrize(
        ("category", "symbol", "product_type"),
        [
            ("linear", "BTCUSDT-25DEC26", "FUTURE"),
            ("inverse", "BTCUSDZ26", "INVERSE-FUTURE"),
        ],
    )
    def test_future(self, category: str, symbol: str, product_type: str):
        market = next(m for m in parse_markets(category) if m["symbol"] == symbol)
        assert market["product_type"] == ProductType.model_validate(product_type)
        assert market["expiration"] == datetime(2026, 12, 25, 8, tzinfo=UTC)
        assert market["funding_interval"] is None  # fundingInterval is 0

    def test_option(self):
        assert parse_markets("option")[0] == {
            "symbol": "BTC-24SEP27-65000-P-USDT",
            "base_asset": "BTC",
            "quote_asset": "USDT",
            "product_type": ProductType.model_validate("OPTION"),
            "option_type": OptionType.PUT,
            "strike_price": Decimal(65000),
            "tick_size": Decimal(5),
            "lot_size": Decimal("0.01"),
            "min_price": Decimal(5),
            "max_price": Decimal(1110000),
            "min_quantity": Decimal("0.01"),
            "max_quantity": Decimal(500),
            "listed_at": datetime(2026, 10, 24, 15, 30, tzinfo=UTC),
            "expiration": datetime(2027, 9, 24, 8, tzinfo=UTC),
            "settlement_currency": "USDT",
        }

    @pytest.mark.parametrize("category", CATEGORIES)
    def test_all_markets_are_valid_base_markets(self, category: str):
        response = load(category)
        markets = parse_markets(category)
        assert len(markets) == len(response["result"]["list"])
        for market in markets:
            BaseMarket.model_validate(market)

    def test_unknown_contract_type_raises(self):
        response = load("linear")
        response["result"]["list"][0]["contractType"] = "UnknownType"
        with pytest.raises(SchemaParseError, match=r"data\[0\]\.product_type: .* failed at _to_product_type: KeyError"):
            BybitParser().parse_markets(response)

    def test_unknown_category_raises(self):
        response = load("spot")
        response["result"]["category"] = "unknown"
        with pytest.raises(KeyError):
            BybitParser().parse_markets(response)

    def test_option_type_and_strike_price(self):
        markets = {m["symbol"]: m for m in parse_markets("option")}
        assert markets["BTC-24SEP27-65000-C-USDT"]["option_type"] == OptionType.CALL
        assert markets["BTC-24SEP27-170000-P-USDT"]["option_type"] == OptionType.PUT
        assert markets["BTC-24SEP27-170000-P-USDT"]["strike_price"] == Decimal(170000)


class TestToDatetime:
    @pytest.mark.parametrize("ms", ["0", "-1"])
    def test_non_positive_is_none(self, ms: str):
        assert BybitParser._to_datetime(ms) is None

    def test_ms_timestamp(self):
        assert BybitParser._to_datetime("1798185600000") == datetime(2026, 12, 25, 8, tzinfo=UTC)

    @pytest.mark.parametrize("ms", ["", "abc"])
    def test_non_numeric_raises(self, ms: str):
        with pytest.raises(ValueError):
            BybitParser._to_datetime(ms)


class TestParseCandlestick:
    def test_bar(self):
        [bar] = BybitParser().parse_candlestick(load_message("kline_linear"))
        assert {k: v for k, v in bar.to_dict().items() if k != "created_at"} == {
            "resolution": Resolution("1m"),
            "start_ts": 1793707560000_000_000,
            # Bybit's inclusive end 1793707619999 -> exclusive end
            "end_ts": 1793707620000_000_000,
            "updated_ts": 1793707610671_000_000,
            "server_ts": 1793707610671_000_000,
            "open": 77047.7,
            "high": 77049.9,
            "low": 77010.1,
            "close": 77026.6,
            "volume": 4.949,
            "is_closed": False,
        }

    def test_closed_bar(self):
        message = load_message("kline_linear")
        message["data"][0]["confirm"] = True
        [bar] = BybitParser().parse_candlestick(message)
        assert bar.is_closed

    def test_daily_bar(self):
        message = load_message("kline_linear")
        message["topic"] = "kline.D.BTCUSDT"
        message["data"][0] |= {"start": 1793664000000, "end": 1793750399999, "interval": "D"}
        [bar] = BybitParser().parse_candlestick(message)
        assert bar.resolution == Resolution("1d")
        assert bar.end_ts - bar.start_ts == 86400 * 1_000_000_000

    def test_parse_message_routes_kline(self, mocker: MockerFixture):
        parser = BybitParser()
        spy = mocker.spy(parser, "parse_candlestick")
        message = load_message("kline_linear")
        assert parser.parse_message(message["topic"], message) == spy.spy_return
        spy.assert_called_once_with(message)

    def test_unknown_interval_raises(self):
        message = load_message("kline_linear")
        message["topic"] = "kline.2.BTCUSDT"
        with pytest.raises(KeyError):
            BybitParser().parse_candlestick(message)

    def test_invalid_bar_raises(self):
        message = load_message("kline_linear")
        message["data"][0]["high"] = "1"  # below open, low and close
        with pytest.raises(ValidationError, match="high"):
            BybitParser().parse_candlestick(message)


class TestParseTradebook:
    def test_ticks(self):
        ticks = BybitParser().parse_tradebook(load_message("public_trade_linear"))
        assert [{k: v for k, v in tick.to_dict().items() if k != "created_at"} for tick in ticks] == [
            {
                "ts": 1793708150596_000_000,
                "server_ts": 1793708150597_000_000,
                "price": 77220.0,
                "volume": volume,
                "side": Side.BUY,
            }
            for volume in (0.131, 0.133, 0.001)
        ]

    def test_option_tick(self):
        [tick] = BybitParser().parse_tradebook(load_message("public_trade_option"))
        assert (tick.ts, tick.price, tick.volume, tick.side) == (1793708151669_000_000, 615.0, 0.08, Side.SELL)

    def test_parse_message_routes_public_trade(self, mocker: MockerFixture):
        parser = BybitParser()
        spy = mocker.spy(parser, "parse_tradebook")
        message = load_message("public_trade_linear")
        assert parser.parse_message(message["topic"], message) == spy.spy_return
        spy.assert_called_once_with(message)

    def test_unknown_side_raises(self):
        message = load_message("public_trade_linear")
        message["data"][0]["S"] = "Unknown"
        with pytest.raises(SchemaParseError, match=r"data\[0\]\.side: .* failed at _to_side: KeyError"):
            BybitParser().parse_tradebook(message)

    def test_negative_volume_raises(self):
        message = load_message("public_trade_linear")
        message["data"][0]["v"] = "-1"
        with pytest.raises(ValidationError, match="volume"):
            BybitParser().parse_tradebook(message)
