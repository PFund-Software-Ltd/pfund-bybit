"""Bybit's narrowed versions of pfund_blocks.typing's venue-agnostic aliases."""

from __future__ import annotations

from typing import Any

# Bybit's ws messages are always JSON objects
type DecodedMessage = dict[str, Any]
# Bybit v5's responses are always JSON objects, e.g. {"retCode": 0, "retMsg": "OK", "result": {...}}
type RawResponse = dict[str, Any]
