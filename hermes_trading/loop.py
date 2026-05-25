"""24/7 reliability loop — pulls data, evaluates strategy, paper-trades, logs."""
import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

from hermes_trading.adapters.price import fetch as price_fetch
from hermes_trading.adapters.onchain import fetch as onchain_fetch
from hermes_trading.adapters.news import fetch as news_fetch
from hermes_trading.adapters.macro import fetch as macro_fetch
from hermes_trading.score import score

logger = logging.getLogger(__name__)

STATE_DIR = Path(__file__).parent.parent / "state"
STRATEGY_PATH = STATE_DIR / "strategy.yaml"
TRADES_PATH = STATE_DIR / "trades.jsonl"
HEARTBEAT_PATH = STATE_DIR / "heartbeat.json"
GOAL_PATH = STATE_DIR / "goal.yaml"

ADAPTERS = [
    ("price", price_fetch),
    ("onchain", onchain_fetch),
    ("news", news_fetch),
    ("macro", macro_fetch),
]

MAX_RETRIES = 3
CIRCUIT_BREAK_THRESHOLD = 5
LOOP_INTERVAL_SECONDS = 60


async def _fetch_with_retry(name: str, fn) -> dict | None:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            data = await fn()
            return data
        except Exception as exc:
            wait = 2 ** attempt
            logger.warning("adapter=%s attempt=%d error=%s retry_in=%ds", name, attempt, exc, wait)
            if attempt < MAX_RETRIES:
                await asyncio.sleep(wait)
    logger.error("adapter=%s failed after %d retries", name, MAX_RETRIES)
    return None


def _load_strategy() -> dict:
    return yaml.safe_load(STRATEGY_PATH.read_text())


def _evaluate_entry(strategy: dict, price_data: dict) -> bool:
    """Return True when entry conditions are met."""
    entry = strategy.get("entry", {})
    indicator = entry.get("indicator", "rsi")
    threshold = entry.get("threshold", 30)
    direction = entry.get("direction", "long")

    if indicator == "rsi":
        rsi = price_data.get("rsi")
        if rsi is None:
            return False
        if direction == "long":
            return rsi < threshold
        else:
            return rsi > threshold
    return False


def _paper_trade(asset: str, strategy: dict, price_data: dict) -> dict | None:
    """Execute a paper trade; return trade record or None."""
    if not _evaluate_entry(strategy, price_data):
        return None

    entry_price = price_data.get("close", 0.0)
    if entry_price <= 0:
        return None

    stop_loss_pct = strategy.get("stop_loss_pct", 2.0) / 100
    direction = strategy.get("entry", {}).get("direction", "long")

    if direction == "long":
        exit_price = entry_price * (1 - stop_loss_pct)
        pnl_pct = (exit_price - entry_price) / entry_price
    else:
        exit_price = entry_price * (1 + stop_loss_pct)
        pnl_pct = (entry_price - exit_price) / entry_price

    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "asset": asset,
        "strategy_version": strategy.get("version", "01"),
        "direction": direction,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "pnl_pct": round(pnl_pct, 6),
        "rsi": price_data.get("rsi"),
        "closed": True,
    }


def _append_trade(trade: dict) -> None:
    with TRADES_PATH.open("a") as f:
        f.write(json.dumps(trade) + "\n")


def _write_heartbeat(asset: str, consecutive_failures: int) -> None:
    HEARTBEAT_PATH.write_text(
        json.dumps(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "asset": asset,
                "status": "ok" if consecutive_failures == 0 else "degraded",
                "consecutive_failures": consecutive_failures,
            },
            indent=2,
        )
    )


async def run_loop(asset: str, goal: dict) -> None:
    consecutive_failures = 0

    while True:
        tick_start = time.monotonic()

        market_data: dict = {}
        for name, fn in ADAPTERS:
            result = await _fetch_with_retry(name, fn)
            if result is None:
                consecutive_failures += 1
                logger.error("circuit_check consecutive_failures=%d", consecutive_failures)
                if consecutive_failures >= CIRCUIT_BREAK_THRESHOLD:
                    logger.critical("Circuit breaker tripped — halting loop")
                    _write_heartbeat(asset, consecutive_failures)
                    raise RuntimeError("Circuit breaker tripped after 5 consecutive failures")
            else:
                consecutive_failures = 0
                market_data[name] = result

        strategy = _load_strategy()
        price_data = market_data.get("price", {})

        trade = _paper_trade(asset, strategy, price_data)
        if trade:
            _append_trade(trade)
            logger.info("paper_trade asset=%s pnl_pct=%.4f rsi=%.1f", asset, trade["pnl_pct"], trade.get("rsi") or 0)

        trades: list[dict] = []
        if TRADES_PATH.exists():
            for line in TRADES_PATH.read_text().splitlines():
                line = line.strip()
                if line:
                    trades.append(json.loads(line))

        current_score = score(trades, goal)
        logger.info("tick asset=%s score=%.3f strategy_v=%s", asset, current_score, strategy.get("version"))

        _write_heartbeat(asset, consecutive_failures)

        elapsed = time.monotonic() - tick_start
        sleep_for = max(0.0, LOOP_INTERVAL_SECONDS - elapsed)
        await asyncio.sleep(sleep_for)
