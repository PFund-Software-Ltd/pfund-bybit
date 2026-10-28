from __future__ import annotations

from typing import Any

import pytest
from pfund_blocks.entities.products.product_base import BaseProduct
from pfund_blocks.entities.products.product_factory import ProductFactory
from pfund_blocks.enums import OrderType, Side
from pfund_blocks.resolution import Resolution

from pfund_bybit.adapter import BybitAdapter


def create(
    basis: str, adapter: BybitAdapter | None = None, **specs: Any
) -> BaseProduct:
    return ProductFactory(basis).model_validate(
        {"source": "BYBIT", "basis": basis, **specs},
        context={"adapter": adapter or BybitAdapter()},
    )


class TestSymbol:
    @pytest.mark.parametrize(
        ("basis", "specs", "symbol"),
        [
            ("BTC_USDT_CRYPTO", {}, "BTCUSDT"),
            ("BTC_USDT_PERP", {}, "BTCUSDT"),
            ("BTC_USDC_PERP", {}, "BTCPERP"),
            ("BTC_USD_IPERP", {}, "BTCUSD"),
            ("BTC_USDT_FUT", {"expiration": "2025-08-22"}, "BTCUSDT-22AUG25"),
            ("BTC_USDC_FUT", {"expiration": "2024-12-13"}, "BTC-13DEC24"),
            ("BTC_USD_IFUT", {"expiration": "2025-03-28"}, "BTCUSDH25"),
            (
                "BTC_USDT_OPT",
                {
                    "expiration": "2024-08-30",
                    "strike_price": "60000.0",
                    "option_type": "call",
                },
                "BTC-30AUG24-60000-C",
            ),
            (
                "ETH_USDT_OPT",
                {
                    "expiration": "2024-08-30",
                    "strike_price": 2500,
                    "option_type": "PUT",
                },
                "ETH-30AUG24-2500-P",
            ),
        ],
    )
    def test_symbol(self, basis: str, specs: dict[str, Any], symbol: str):
        assert create(basis, **specs).symbol == symbol

    def test_explicit_symbol_is_kept(self):
        assert create("BTC_USDT_PERP", symbol="BTC-PERP").symbol == "BTC-PERP"

    def test_asset_mapping_is_used(self):
        adapter = BybitAdapter(asset={"XBT": "BTC"})
        assert create("XBT_USDT_PERP", adapter).symbol == "BTCUSDT"

    @pytest.mark.parametrize(
        ("basis", "specs"),
        [
            ("BTC_USDT_IPERP", {}),
            ("BTC_USDT_IFUT", {"expiration": "2025-03-28"}),
        ],
    )
    def test_inverse_requires_quote_asset_as_usd(
        self, basis: str, specs: dict[str, Any]
    ):
        with pytest.raises(ValueError, match="only USD-denominated inverse contracts"):
            create(basis, **specs)

    def test_future_requires_usdc_or_usdt(self):
        with pytest.raises(ValueError, match="only USDC and USDT futures"):
            create("BTC_EUR_FUT", expiration="2025-08-22")

    def test_unsupported_product_raises(self):
        with pytest.raises(ValueError, match="is not supported by Bybit"):
            create("AAPL_USD_STK")


class TestName:
    def test_spot_and_perpetual_share_a_symbol_but_not_a_name(self):
        spot = create("BTC_USDT_CRYPTO")
        perpetual = create("BTC_USDT_PERP")
        assert spot.symbol == perpetual.symbol == "BTCUSDT"
        assert spot.name == "BYBIT_BTC_USDT_CRYPTO"
        assert perpetual.name == "BYBIT_BTC_USDT_PERPETUAL"

    def test_other_names_are_source_and_symbol(self):
        future = create("BTC_USDT_FUT", expiration="2025-08-22")
        assert future.name == "BYBIT_BTCUSDT-22AUG25"

    def test_explicit_name_is_kept(self):
        assert create("BTC_USDT_PERP", name="BTC").name == "BTC"


class TestContractType:
    @pytest.mark.parametrize(
        ("basis", "specs", "contract_type"),
        [
            ("BTC_USDT_PERP", {}, "LinearPerpetual"),
            ("BTC_USD_IPERP", {}, "InversePerpetual"),
            ("BTC_USDT_FUT", {"expiration": "2025-08-22"}, "LinearFutures"),
            ("BTC_USD_IFUT", {"expiration": "2025-03-28"}, "InverseFutures"),
        ],
    )
    def test_both_directions(
        self, basis: str, specs: dict[str, Any], contract_type: str
    ):
        adapter = BybitAdapter()
        product = create(basis, **specs)
        assert adapter.to_source(product.type, group="product_type") == contract_type
        assert adapter.from_source(contract_type, group="product_type") == product.type

    @pytest.mark.parametrize(
        ("basis", "specs"),
        [
            ("BTC_USDT_CRYPTO", {}),
            (
                "BTC_USDT_OPT",
                {
                    "expiration": "2024-08-30",
                    "strike_price": 60000,
                    "option_type": "call",
                },
            ),
        ],
    )
    def test_spot_and_option_have_no_contract_type(
        self, basis: str, specs: dict[str, Any]
    ):
        product = create(basis, **specs)
        with pytest.raises(KeyError, match="has no source mapping"):
            BybitAdapter().to_source(product.type, group="product_type", strict=True)


class TestMappings:
    def test_static_mappings(self):
        adapter = BybitAdapter()
        assert adapter.to_source(Side.BUY, group="side") == "Buy"
        assert adapter.from_source("Market", group="order_type") is OrderType.MARKET
        assert adapter.from_source("Filled", group="order_status") == "CF--"
        assert adapter.to_source(Resolution("1d"), group="channel_resolution") == "D"

    def test_defaults_are_not_shared_between_instances(self):
        adapter = BybitAdapter()
        adapter.add_mapping(OrderType.STOP_MARKET, "StopMarket", group="order_type")
        with pytest.raises(KeyError):
            BybitAdapter().to_source(
                OrderType.STOP_MARKET, group="order_type", strict=True
            )
