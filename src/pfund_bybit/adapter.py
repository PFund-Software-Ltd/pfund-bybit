from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from pfund_blocks.entities.products.future import FutureProduct
    from pfund_blocks.entities.products.option import OptionProduct
    from pfund_blocks.entities.products.product_base import BaseProduct

from pfund_blocks.adapters.adapter_base import ExternalValue
from pfund_blocks.adapters.adapter_venue import (
    OrderStatusRepr,
    VenueAdapter,
)
from pfund_blocks.entities.products.product_type import ProductType
from pfund_blocks.enums import (
    Channel,
    MarginMode,
    OptionType,
    OrderType,
    Side,
)
from pfund_blocks.resolution import Resolution


# NOTE: the defaults are not validated by pydantic, so the keys are created as their types directly, e.g. ProductType, Resolution
# NOTE: pydantic copies mutable defaults per instance, ruff just doesn't see BybitAdapter as a pydantic model
class BybitAdapter(VenueAdapter):
    """Maps pfund's internal values to Bybit's, and derives Bybit's product symbols and names.

    Usage:
        Product.model_validate({...}, context={"adapter": adapter})  # product with Bybit's symbol, e.g. BTCPERP
    """

    # from '/v5/market/instruments-info'
    product_type: dict[ProductType, ExternalValue] = {  # noqa: RUF012
        ProductType.model_validate("PERPETUAL"): "LinearPerpetual",
        ProductType.model_validate("INVERSE-PERPETUAL"): "InversePerpetual",
        ProductType.model_validate("FUTURE"): "LinearFutures",
        ProductType.model_validate("INVERSE-FUTURE"): "InverseFutures",
    }
    side: dict[Side, ExternalValue] = {  # noqa: RUF012
        Side.BUY: "Buy",
        Side.SELL: "Sell",
    }
    margin_mode: dict[MarginMode, ExternalValue] = {  # noqa: RUF012
        MarginMode.ISOLATED: "ISOLATED_MARGIN",
        MarginMode.CROSS: "REGULAR_MARGIN",
        MarginMode.PORTFOLIO: "PORTFOLIO_MARGIN",
    }
    order_status: dict[OrderStatusRepr, ExternalValue] = {  # noqa: RUF012
        "S---": "Submitted",
        "R---": "Rejected",
        "A---": "New",
        "AP--": "PartiallyFilled",
        "CPC-": "PartiallyFilledCanceled",
        "CF--": "Filled",
        "C-C-": "Cancelled",
    }
    order_type: dict[OrderType, ExternalValue] = {  # noqa: RUF012
        OrderType.LIMIT: "Limit",
        OrderType.MARKET: "Market",
    }
    option_type: dict[OptionType, ExternalValue] = {  # noqa: RUF012
        OptionType.CALL: "Call",
        OptionType.PUT: "Put",
    }
    channel: dict[Channel, ExternalValue] = {  # noqa: RUF012
        Channel.orderbook: "orderbook",
        Channel.tradebook: "publicTrade",
        Channel.candlestick: "kline",
        Channel.balance: "wallet",
        Channel.position: "position",
        Channel.order: "order",
        Channel.trade: "execution",
    }
    channel_resolution: dict[Resolution, ExternalValue] = {  # noqa: RUF012
        Resolution("1m"): "1",
        Resolution("3m"): "3",
        Resolution("5m"): "5",
        Resolution("15m"): "15",
        Resolution("30m"): "30",
        Resolution("60m"): "60",
        Resolution("120m"): "120",
        Resolution("240m"): "240",
        Resolution("360m"): "360",
        Resolution("720m"): "720",
        Resolution("1d"): "D",
        Resolution("1w"): "W",
        Resolution("1mo"): "M",
    }

    def create_symbol(self, product: BaseProduct) -> str:
        # e = external, i.e. Bybit's
        ebase_asset = self.to_source(product.base_asset, group="asset")
        equote_asset = self.to_source(product.quote_asset, group="asset")
        # e.g. BTCUSDT
        if product.is_crypto_spot():
            return ebase_asset + equote_asset
        if product.is_perpetual():
            # e.g. BTCUSD
            if product.is_inverse():
                self._require_quote_asset_as_usd(product, equote_asset)
                return ebase_asset + equote_asset
            # e.g. BTCPERP for USDC, BTCUSDT for USDT
            if equote_asset == "USDC":
                return ebase_asset + "PERP"
            return ebase_asset + equote_asset
        if product.is_future():
            future = cast("FutureProduct", product)
            # e.g. BTCUSDH25
            if product.is_inverse():
                self._require_quote_asset_as_usd(product, equote_asset)
                return ebase_asset + equote_asset + future.contract_code
            expiration = f"{future.expiration:%d%b%y}".upper()
            # e.g. BTC-13DEC24
            if equote_asset == "USDC":
                return f"{ebase_asset}-{expiration}"
            # e.g. BTCUSDT-22AUG25
            if equote_asset == "USDT":
                return f"{ebase_asset}{equote_asset}-{expiration}"
            raise ValueError(
                f"only USDC and USDT futures are supported, not {equote_asset} in {product.basis}"
            )
        if product.is_option():
            option = cast("OptionProduct", product)
            # e.g. BTC-30AUG24-60000-C
            expiration = f"{option.expiration:%d%b%y}".upper()
            return f"{ebase_asset}-{expiration}-{option.strike_price}-{option.option_type[0]}"
        raise ValueError(f"{product.basis} is not supported by Bybit")

    @staticmethod
    def _require_quote_asset_as_usd(product: BaseProduct, equote_asset: str) -> None:
        if equote_asset != "USD":
            raise ValueError(
                f"only USD-denominated inverse contracts are supported, did you mean {product.base_asset}_USD_{product.product_type}?"
            )

    def create_name(self, product: BaseProduct) -> str:
        # NOTE: spots and perpetuals share symbols (e.g. BTCUSDT), so the basis is used to make their names unique
        if product.is_crypto_spot() or product.is_perpetual():
            return f"{product.source}_{product.basis}"
        return super().create_name(product)
