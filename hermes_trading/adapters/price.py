"""Price adapter — OHLCV + RSI via ccxt (free public endpoint)."""
import os
import asyncio
from typing import Any

SCHEMA_VERSION = "price/v1"


class SchemaError(Exception):
    pass


def _compute_rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d for d in deltas if d > 0]
    losses = [-d for d in deltas if d < 0]
    avg_gain = sum(gains[-period:]) / period if gains else 0.0
    avg_loss = sum(losses[-period:]) / period if losses else 0.0
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)


async def fetch() -> dict[str, Any]:
    import ccxt.async_support as ccxt

    asset = os.environ.get("HERMES_ASSET", "SOL/USDT")
    exchange_id = os.environ.get("HERMES_EXCHANGE", "binance")

    exchange_cls = getattr(ccxt, exchange_id)
    kwargs: dict = {"enableRateLimit": True}
    api_key = os.environ.get("EXCHANGE_API_KEY")
    api_secret = os.environ.get("EXCHANGE_API_SECRET")
    if api_key and api_secret:
        kwargs["apiKey"] = api_key
        kwargs["secret"] = api_secret

    exchange = exchange_cls(kwargs)
    try:
        ohlcv = await exchange.fetch_ohlcv(asset, timeframe="1m", limit=30)
    finally:
        await exchange.close()

    if not ohlcv:
        raise SchemaError(f"price adapter: empty OHLCV for {asset}")

    closes = [candle[4] for candle in ohlcv]
    latest = ohlcv[-1]

    return {
        "schema_version": SCHEMA_VERSION,
        "asset": asset,
        "open": latest[1],
        "high": latest[2],
        "low": latest[3],
        "close": latest[4],
        "volume": latest[5],
        "rsi": _compute_rsi(closes),
        "closes_30": closes,
    }
