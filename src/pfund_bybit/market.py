from enum import StrEnum

from pfund_blocks.entities import BaseMarket


# NOTE: defined here instead of in venue.py, since venue.py imports this module
class Category(StrEnum):
    # values are Bybit's, e.g. '/v5/market/instruments-info?category=linear'
    LINEAR = "linear"
    INVERSE = "inverse"
    SPOT = "spot"
    OPTION = "option"


class BybitMarket(BaseMarket):
    category: Category
