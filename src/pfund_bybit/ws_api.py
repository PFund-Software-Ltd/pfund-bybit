from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Literal

if TYPE_CHECKING:
    from pfund_blocks.apis.ws_api_base import WebSocket
    from pfund_blocks.entities.accounts.account_base import BaseAccount
    from pfund_blocks.entities.products.product_base import BaseProduct
    from pfund_blocks.typing import (
        DecodedMessage,
        FullChannel,
        RawMessage,
        WebSocketName,
    )

from msgspec import json
from pfund_blocks.apis.ws_api_base import BaseWebSocketAPI
from pfund_blocks.enums import Channel, ChannelType, Environment
from pfund_blocks.enums.timeframe import Timeframe
from pfund_blocks.resolution import Resolution

from pfund_bybit.parser import BybitParser
from pfund_bybit.venue import Bybit


class BybitWebSocketAPI(BaseWebSocketAPI):
    """Bybit's websocket API of one category (spot, linear, inverse or option),
    since Bybit has one public endpoint per category.
    """

    venue: ClassVar[str] = "BYBIT"
    version: ClassVar[str | None] = "v5"
    # NOTE: {category} is filled in _get_url(), the private endpoint is shared by all categories
    urls = {  # noqa: RUF012
        Environment.PAPER: {
            ChannelType.public: "wss://stream-testnet.bybit.com/v5/public/{category}",
            ChannelType.private: "wss://stream-testnet.bybit.com/v5/private",
        },
        Environment.LIVE: {
            ChannelType.public: "wss://stream.bybit.com/v5/public/{category}",
            ChannelType.private: "wss://stream.bybit.com/v5/private",
        },
    }
    parser: ClassVar[BybitParser] = BybitParser()  # ty: ignore[invalid-mutable-override]
    ping_message: ClassVar[dict[str, Any] | None] = {"op": "ping"}
    # max number of public channels in the "args" of one (un)subscription request, unlimited if not listed
    # NOTE: private channels have no limit
    # REVIEW: option's limit
    MAX_PUBLIC_CHANNELS_PER_SUBSCRIPTION: ClassVar[dict[Bybit.Category, int]] = {
        Bybit.Category.SPOT: 10,
        Bybit.Category.OPTION: 2000,
    }
    # key -> periods, e.g. MINUTE: [1, 3] means 1m and 3m
    # NOTE: for quotes, period = orderbook depth per side, e.g. QUOTE_L2: [50] means 50q_L2 = "orderbook.50.BTCUSDT"
    SUPPORTED_RESOLUTIONS: ClassVar[
        dict[Bybit.Category, dict[Resolution | Timeframe, list[int]]]
    ] = {
        Bybit.Category.LINEAR: {
            Resolution("QUOTE_L1"): [1],
            Resolution("QUOTE_L2"): [1, 50, 200, 500],
            Timeframe.TICK: [1],
            Timeframe.MINUTE: [1, 3, 5, 15, 30, 60, 120, 240, 360, 720],
            Timeframe.DAY: [1],
        },
        Bybit.Category.INVERSE: {
            Resolution("QUOTE_L1"): [1],
            Resolution("QUOTE_L2"): [1, 50, 200, 500],
            Timeframe.TICK: [1],
            Timeframe.MINUTE: [1, 3, 5, 15, 30, 60, 120, 240, 360, 720],
            Timeframe.DAY: [1],
        },
        Bybit.Category.SPOT: {
            Resolution("QUOTE_L1"): [1],
            Resolution("QUOTE_L2"): [1, 50],
            Timeframe.TICK: [1],
            Timeframe.MINUTE: [1, 3, 5, 15, 30, 60, 120, 240, 360, 720],
            Timeframe.DAY: [1],
        },
        # REVIEW: verify that options have klines
        Bybit.Category.OPTION: {
            # NOTE: option trading doesn't support QUOTE_L1
            Resolution("QUOTE_L2"): [25, 100],
            Timeframe.TICK: [1],
            Timeframe.MINUTE: [1, 3, 5, 15, 30, 60, 120, 240, 360, 720],
            Timeframe.DAY: [1],
        },
    }

    def __init__(
        self,
        env: Literal[Environment.PAPER, Environment.LIVE, "PAPER", "LIVE"],
        category: Bybit.Category | Literal["spot", "linear", "inverse", "option"],
    ):
        self._category = Bybit.Category(category.lower())
        super().__init__(env)

    @property
    def category(self) -> Bybit.Category:
        return self._category

    def _get_url(self, channel_type: ChannelType) -> str:
        return super()._get_url(channel_type).format(category=self._category)

    def _create_ws_name(self, account: BaseAccount | None = None) -> WebSocketName:
        # e.g. "bybit_spot_ws", so that the category can be parsed back from the ws name
        if account:
            return super()._create_ws_name(account)
        return f"{self.venue.lower()}_{self._category}_ws"

    def get_supported_resolutions(self, product: BaseProduct) -> list[Resolution]:
        resolutions: list[Resolution] = []
        for key, periods in self.SUPPORTED_RESOLUTIONS[Bybit.get_category(product)].items():
            if isinstance(key, Timeframe):
                resolutions += [Resolution(f"{period}{key.canonical}") for period in periods]
            else:
                resolutions += [Resolution(f"{period}q_L{key.orderbook_level}") for period in periods]
        return resolutions

    def _create_market_data_channel(self, product: BaseProduct, resolution: Resolution) -> FullChannel:
        if (category := Bybit.get_category(product)) != self._category:
            raise ValueError(f"{product.name} is in category {category}, not {self._category}")
        adapter = self.parser.adapter
        if resolution.is_bar():
            # e.g. "kline.1.BTCUSDT"
            echannel = adapter.to_source(Channel.candlestick, group="channel", strict=True)
            eresolution = adapter.to_source(resolution, group="channel_resolution", strict=True)
            return f"{echannel}.{eresolution}.{product.symbol}"
        if resolution.is_tick():
            # e.g. "publicTrade.BTCUSDT"
            echannel = adapter.to_source(Channel.tradebook, group="channel", strict=True)
            return f"{echannel}.{product.symbol}"
        if resolution.is_quote():
            # e.g. "orderbook.50.BTCUSDT", the period of a quote resolution is the orderbook depth
            echannel = adapter.to_source(Channel.orderbook, group="channel", strict=True)
            return f"{echannel}.{resolution.period}.{product.symbol}"
        raise NotImplementedError(f"{self.venue} {resolution=} is not supported")

    def _batch_channels(self, channels: list[FullChannel], channel_type: ChannelType) -> list[list[FullChannel]]:
        """Splits public channels into batches within the category's MAX_PUBLIC_CHANNELS_PER_SUBSCRIPTION."""
        if not channels:
            return []
        if channel_type == ChannelType.public:
            limit = self.MAX_PUBLIC_CHANNELS_PER_SUBSCRIPTION.get(self._category)
        else:
            limit = None
        limit = limit or len(channels)
        return [channels[i : i + limit] for i in range(0, len(channels), limit)]

    async def subscribe(self, ws: WebSocket, channels: list[FullChannel], channel_type: ChannelType) -> None:
        for batch in self._batch_channels(channels, channel_type):
            await self._send(ws, {"op": "subscribe", "args": batch})

    async def unsubscribe(self, ws: WebSocket, channels: list[FullChannel], channel_type: ChannelType) -> None:
        for batch in self._batch_channels(channels, channel_type):
            await self._send(ws, {"op": "unsubscribe", "args": batch})

    def _parse_message(self, ws: WebSocket, raw_msg: RawMessage) -> tuple[FullChannel | None, DecodedMessage] | None:
        msg = json.decode(raw_msg)
        # a failed request is Bybit's error, e.g. a rejected subscription
        is_success = msg.get("success")
        if is_success is False:
            return None, msg

        # e.g. {"success": true, "ret_msg": "subscribe", "op": "subscribe", ...}
        if "op" in msg:
            op: str = msg["op"]
            # NOTE: pongs are either {"op": "ping", "success": true, "ret_msg": "pong"} or {"op": "pong", ...} without "success"
            if op in ("subscribe", "unsubscribe") or op == "pong" or msg.get("ret_msg") == "pong":
                return None
            else:
                self._logger.warning(f"{ws.name} unhandled msg {msg}")
            return None
        elif "topic" in msg:
            # e.g. {"topic": "kline.1.BTCUSDT", "type": "snapshot", "ts": 1672324988882, "data": [...]}
            return msg["topic"], msg
        else:
            self._logger.warning(f"{ws.name} unhandled msg {msg}")
            return None
