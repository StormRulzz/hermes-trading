"""News sentiment adapter — CoinGecko trending + fear/greed index (free, no key required)."""
import os
from typing import Any

import httpx

SCHEMA_VERSION = "news/v1"
FEAR_GREED_URL = "https://api.alternative.me/fng/?limit=1"
TRENDING_URL = "https://api.coingecko.com/api/v3/search/trending"


class SchemaError(Exception):
    pass


async def fetch() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            fg_resp = await client.get(FEAR_GREED_URL)
            fg_resp.raise_for_status()
            fg_data = fg_resp.json()
            fg_value = int(fg_data["data"][0]["value"])
            fg_label = fg_data["data"][0]["value_classification"]
        except Exception:
            fg_value = 50
            fg_label = "Neutral"

        try:
            trend_resp = await client.get(TRENDING_URL)
            trend_resp.raise_for_status()
            trend_data = trend_resp.json()
            trending_coins = [c["item"]["symbol"].upper() for c in trend_data.get("coins", [])[:5]]
            sol_trending = "SOL" in trending_coins
        except Exception:
            trending_coins = []
            sol_trending = False

    if fg_value >= 60:
        sentiment = "bullish"
    elif fg_value <= 40:
        sentiment = "bearish"
    else:
        sentiment = "neutral"

    return {
        "schema_version": SCHEMA_VERSION,
        "sentiment": sentiment,
        "fear_greed_value": fg_value,
        "fear_greed_label": fg_label,
        "sol_trending": sol_trending,
        "trending_coins": trending_coins,
        "degraded": False,
    }
