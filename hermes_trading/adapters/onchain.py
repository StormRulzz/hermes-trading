"""On-chain adapter — Solana network stats via public RPC."""
import os
from typing import Any

import httpx

SCHEMA_VERSION = "onchain/v1"
SOLANA_RPC = "https://api.mainnet-beta.solana.com"


class SchemaError(Exception):
    pass


async def fetch() -> dict[str, Any]:
    payload = {"jsonrpc": "2.0", "id": 1, "method": "getEpochInfo"}

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(SOLANA_RPC, json=payload)
        resp.raise_for_status()
        data = resp.json()

    result = data.get("result")
    if not result or "absoluteSlot" not in result:
        raise SchemaError("onchain adapter: unexpected RPC response schema")

    return {
        "schema_version": SCHEMA_VERSION,
        "absolute_slot": result["absoluteSlot"],
        "epoch": result["epoch"],
        "slot_index": result["slotIndex"],
        "slots_in_epoch": result["slotsInEpoch"],
        "transaction_count": result.get("transactionCount", 0),
    }
