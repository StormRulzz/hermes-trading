"""Macro adapter — BTC dominance and global crypto market cap via CoinGecko public API."""
import os
from typing import Any

import httpx

SCHEMA_VERSION = "macro/v1"
COINGECKO_GLOBAL = "https://api.coingecko.com/api/v3/global"


class SchemaError(Exception):
    pass


async def fetch() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(COINGECKO_GLOBAL)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            # graceful degrade — macro is additive signal only
            return {"schema_version": SCHEMA_VERSION, "btc_dominance": 50.0, "total_market_cap_usd": 0, "degraded": True}

    result = data.get("data", {})
    if not result:
        raise SchemaError("macro adapter: unexpected CoinGecko response schema")

    market_cap_pct = result.get("market_cap_percentage", {})
    btc_dom = market_cap_pct.get("btc", 50.0)
    total_mcap = result.get("total_market_cap", {}).get("usd", 0)
    mcap_change_24h = result.get("market_cap_change_percentage_24h_usd", 0.0)

    return {
        "schema_version": SCHEMA_VERSION,
        "btc_dominance": round(btc_dom, 2),
        "total_market_cap_usd": total_mcap,
        "market_cap_change_24h_pct": round(mcap_change_24h, 4),
    }
