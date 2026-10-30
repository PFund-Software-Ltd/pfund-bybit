from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, cast

if TYPE_CHECKING:
    from pfund_blocks.entities.products.product_base import BaseProduct

import asyncio

import pytest
from msgspec import json
from pfund_blocks.apis.ws_api_base import WebSocket
from pfund_blocks.entities.accounts.api_account import APIKeyAccount
from pfund_blocks.entities.products.product_factory import ProductFactory
from pfund_blocks.enums import ChannelType
from pfund_blocks.resolution import Resolution

from pfund_bybit.adapter import BybitAdapter
from pfund_bybit.venue import Bybit
from pfund_bybit.ws_api import BybitWebSocketAPI


def create(basis: str, **specs: Any) -> BaseProduct:
    return ProductFactory(basis).model_validate(
        {"source": "BYBIT", "basis": basis, **specs},
        context={"adapter": BybitAdapter()},
    )


def create_ws(name: str = "bybit_linear_ws") -> WebSocket:
    ws = object.__new__(WebSocket)
    ws.name = name
    return ws


@pytest.fixture
def api() -> BybitWebSocketAPI:
    return BybitWebSocketAPI("LIVE", "linear")


class TestCategory:
    def test_category_is_case_insensitive(self):
        assert BybitWebSocketAPI("LIVE", cast("Any", "SPOT")).category == Bybit.Category.SPOT

    @pytest.mark.parametrize("category", list(Bybit.Category))
    def test_category_is_parsed_back_from_the_ws_name(self, category: Bybit.Category):
        ws_name = BybitWebSocketAPI("LIVE", category)._create_ws_name()
        assert ws_name == f"bybit_{category}_ws"
        assert Bybit.Category(ws_name.split("_")[1]) == category

    def test_private_ws_name_is_per_account(self, api: BybitWebSocketAPI):
        account = APIKeyAccount(env="LIVE", venue="BYBIT", name="acc", key="key", secret="secret")
        assert api._create_ws_name(account) == "acc_ws"

    @pytest.mark.parametrize(
        ("env", "url"),
        [
            ("LIVE", "wss://stream.bybit.com/v5/public/spot"),
            ("PAPER", "wss://stream-testnet.bybit.com/v5/public/spot"),
        ],
    )
    def test_public_url_per_category(self, env: Literal["LIVE", "PAPER"], url: str):
        assert BybitWebSocketAPI(env, "spot")._get_url(ChannelType.public) == url

    def test_private_url_is_shared(self):
        urls = {BybitWebSocketAPI("LIVE", category)._get_url(ChannelType.private) for category in Bybit.Category}
        assert urls == {"wss://stream.bybit.com/v5/private"}


class TestCreateMarketDataChannel:
    @pytest.mark.parametrize(
        ("resolution", "channel"),
        [
            ("1m", "kline.1.BTCUSDT"),
            ("1h", "kline.60.BTCUSDT"),
            ("1d", "kline.D.BTCUSDT"),
            ("1t", "publicTrade.BTCUSDT"),
        ],
    )
    def test_channel(self, api: BybitWebSocketAPI, resolution: str, channel: str):
        assert api.create_market_data_channel(create("BTC_USDT_PERP"), Resolution(resolution)) == channel

    def test_product_of_another_category_raises(self, api: BybitWebSocketAPI):
        with pytest.raises(ValueError, match="is in category spot, not linear"):
            api.create_market_data_channel(create("BTC_USDT_CRYPTO"), Resolution("1m"))

    def test_unsupported_bar_raises(self, api: BybitWebSocketAPI):
        with pytest.raises(ValueError, match="is not supported, supported resolutions"):
            api.create_market_data_channel(create("BTC_USDT_PERP"), Resolution("2m"))

    def test_option_channels(self):
        api = BybitWebSocketAPI("LIVE", "option")
        option = create("BTC_USDT_OPT", expiration="2024-08-30", strike_price=60000, option_type="call")
        assert api.create_market_data_channel(option, Resolution("1m")) == "kline.1.BTC-30AUG24-60000-C"
        assert api.create_market_data_channel(option, Resolution("1t")) == "publicTrade.BTC-30AUG24-60000-C"

    @pytest.mark.parametrize(
        ("resolution", "channel"),
        [
            ("1q_L1", "orderbook.1.BTCUSDT"),
            ("1q_L2", "orderbook.1.BTCUSDT"),
            ("50q_L2", "orderbook.50.BTCUSDT"),
            ("500q_L2", "orderbook.500.BTCUSDT"),
        ],
    )
    def test_orderbook_channel(self, api: BybitWebSocketAPI, resolution: str, channel: str):
        assert api.create_market_data_channel(create("BTC_USDT_PERP"), Resolution(resolution)) == channel

    def test_unsupported_orderbook_depth_raises(self, api: BybitWebSocketAPI):
        with pytest.raises(ValueError, match="is not supported"):
            api.create_market_data_channel(create("BTC_USDT_PERP"), Resolution("100q_L2"))

    def test_option_has_no_l1_orderbook(self):
        api = BybitWebSocketAPI("LIVE", "option")
        option = create("BTC_USDT_OPT", expiration="2024-08-30", strike_price=60000, option_type="call")
        with pytest.raises(ValueError, match="is not supported"):
            api.create_market_data_channel(option, Resolution("1q_L1"))


class TestSupportedResolutions:
    def test_expanded_from_timeframe_periods(self, api: BybitWebSocketAPI):
        resolutions = api.get_supported_resolutions(create("BTC_USDT_PERP"))
        assert Resolution("1q_L1") in resolutions
        assert Resolution("1q_L2") in resolutions
        assert Resolution("50q_L2") in resolutions
        assert Resolution("500q_L2") in resolutions
        assert Resolution("1t") in resolutions
        assert Resolution("1h") in resolutions  # = 60m
        assert Resolution("1d") in resolutions
        assert len(resolutions) == 17

    def test_every_supported_bar_has_a_bybit_interval(self, api: BybitWebSocketAPI):
        # NOTE: otherwise a supported resolution fails when creating its channel
        for category in Bybit.Category:
            for resolution in api.get_supported_resolutions(create(_BASIS[category], **_SPECS.get(category, {}))):
                if resolution.is_bar():
                    assert resolution in api.parser.adapter.channel_resolution


_BASIS = {
    Bybit.Category.LINEAR: "BTC_USDT_PERP",
    Bybit.Category.INVERSE: "BTC_USD_IPERP",
    Bybit.Category.SPOT: "BTC_USDT_CRYPTO",
    Bybit.Category.OPTION: "BTC_USDT_OPT",
}
_SPECS = {Bybit.Category.OPTION: {"expiration": "2024-08-30", "strike_price": 60000, "option_type": "call"}}


async def subscribe(api: BybitWebSocketAPI, channels: list[str], channel_type: ChannelType) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    async def _send(ws: WebSocket, msg: dict[str, Any]) -> None:
        sent.append(msg)

    api._send = _send  # ty: ignore[invalid-assignment]
    await api.subscribe(create_ws(), channels, channel_type)
    return sent


class TestSubscribe:
    @pytest.mark.parametrize(("category", "num_requests"), [("spot", 3), ("linear", 1)])
    def test_public_channels_are_batched(self, category: Literal["spot", "linear"], num_requests: int):
        channels = [f"publicTrade.COIN{i}USDT" for i in range(25)]
        sent = asyncio.run(subscribe(BybitWebSocketAPI("LIVE", category), channels, ChannelType.public))
        assert len(sent) == num_requests
        assert [channel for msg in sent for channel in msg["args"]] == channels

    def test_private_channels_are_not_batched(self):
        channels = [f"channel{i}" for i in range(25)]
        sent = asyncio.run(subscribe(BybitWebSocketAPI("LIVE", "spot"), channels, ChannelType.private))
        assert sent == [{"op": "subscribe", "args": channels}]

    def test_no_channels_sends_nothing(self, api: BybitWebSocketAPI):
        assert asyncio.run(subscribe(api, [], ChannelType.public)) == []


class TestParseMessage:
    @pytest.mark.parametrize(
        "msg",
        [
            {"success": True, "ret_msg": "subscribe", "conn_id": "x", "op": "subscribe"},
            {"success": True, "ret_msg": "pong", "conn_id": "x", "op": "ping"},
            {"op": "pong", "args": ["1675418560633"], "conn_id": "x"},
        ],
    )
    def test_control_messages_are_dropped(self, api: BybitWebSocketAPI, msg: dict[str, Any]):
        assert api._parse_message(create_ws(), json.encode(msg)) is None

    def test_failed_subscription_is_an_error(self, api: BybitWebSocketAPI):
        msg = {"success": False, "ret_msg": "error:handler not found,topic:kline.1.X", "op": "subscribe"}
        result = api._parse_message(create_ws(), json.encode(msg))
        assert result is not None
        channel, decoded = result
        assert channel is None
        assert api.parser.parse_message_error(channel, decoded) == "error:handler not found,topic:kline.1.X"

    def test_data_message(self, api: BybitWebSocketAPI):
        msg = {"topic": "kline.1.BTCUSDT", "type": "snapshot", "ts": 1672324988882, "data": []}
        result = api._parse_message(create_ws(), json.encode(msg))
        assert result is not None
        channel, decoded = result
        assert channel == "kline.1.BTCUSDT"
        assert api.parser.parse_message_error(channel, decoded) is None
