from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from pfund_blocks.entities.products.product_base import BaseProduct

from enum import StrEnum

from pfund_blocks.entities.products.product_type import ProductType
from pfund_blocks.entities.venues.venue_base import BaseVenue, VenueMetadata
from pfund_blocks.enums.env import Environment

from pfund_bybit.adapter import BybitAdapter


# DRAFT: driven by the unified interface in pfund-blocks' BaseVenue
class Bybit(BaseVenue):
    class Category(StrEnum):
        # values are Bybit's, e.g. '/v5/market/instruments-info?category=linear'
        LINEAR = "linear"
        INVERSE = "inverse"
        SPOT = "spot"
        OPTION = "option"

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

    def __init__(self):
        self.adapter = BybitAdapter()

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
