from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from pfund_blocks.enums.env import Environment

from pfund_blocks.entities.accounts import APIKeyAccount


class BybitAccount(APIKeyAccount):
    def __init__(
        self,
        name: str = "",
        key: str = "",
        secret: str = "",
        env: Literal[Environment.PAPER, Environment.LIVE] | None = None,
    ):
        from pfund_bybit.venue import Bybit

        super().__init__(venue=Bybit.metadata.name, name=name, key=key, secret=secret, env=env)

    # NOTE: only supports unified accounts
    @property
    def type(self) -> str:
        return "UNIFIED"
