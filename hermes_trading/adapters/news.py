"""News sentiment adapter — CryptoPanic public feed (free, no key required)."""
import os
from typing import Any

import httpx

SCHEMA_VERSION = "news/v1"
FREE_ENDPOINT = "https://cryptopanic.com/api/v1/posts/?auth_token=free&currencies=SOL&public=true"


class SchemaError(Exception):
    pass


async def fetch() -> dict[str, Any]:
    api_key = os.environ.get("NEWS_API_KEY", "free")
    url = f"https://cryptopanic.com/api/v1/posts/?auth_token={api_key}&currencies=SOL&public=true"

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            # graceful degrade — news is additive signal only
            return {"schema_version": SCHEMA_VERSION, "sentiment": "neutral", "headline_count": 0, "degraded": True}

    results = data.get("results", [])
    if not isinstance(results, list):
        raise SchemaError("news adapter: unexpected response schema")

    bullish = sum(1 for r in results if r.get("votes", {}).get("positive", 0) > r.get("votes", {}).get("negative", 0))
    bearish = sum(1 for r in results if r.get("votes", {}).get("negative", 0) > r.get("votes", {}).get("positive", 0))
    total = len(results)

    if total == 0:
        sentiment = "neutral"
    elif bullish / total > 0.6:
        sentiment = "bullish"
    elif bearish / total > 0.6:
        sentiment = "bearish"
    else:
        sentiment = "neutral"

    return {
        "schema_version": SCHEMA_VERSION,
        "sentiment": sentiment,
        "headline_count": total,
        "bullish": bullish,
        "bearish": bearish,
    }
