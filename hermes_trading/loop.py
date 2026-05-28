"""24/7 reliability loop — pulls data, evaluates strategy, paper-trades, logs."""
import asyncio
import json
import logging
import os
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

STATE_DIR = Path(os.environ.get("HERMES_STATE_DIR", str(Path(__file__).parent.parent / "state")))
STRATEGY_PATH = STATE_DIR / "strategy.yaml"
TRADES_PATH = STATE_DIR / "trades.jsonl"
HEARTBEAT_PATH = STATE_DIR / "heartbeat.json"
POSITION_PATH = STATE_DIR / "position.json"

ADAPTERS = [
    ("price", price_fetch),
    ("onchain", onchain_fetch),
    ("news", news_fetch),
    ("macro", macro_fetch),
]

MAX_RETRIES = 3
CIRCUIT_BREAK_THRESHOLD = 5
LOOP_INTERVAL_SECONDS = 60
RSI_EXIT_THRESHOLD = 50  # exit long when RSI recovers above this


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


def _load_position() -> dict | None:
    if POSITION_PATH.exists():
        try:
            return json.loads(POSITION_PATH.read_text())
        except Exception:
            return None
    return None


def _save_position(position: dict) -> None:
    POSITION_PATH.write_text(json.dumps(position))


def _close_position() -> None:
    if POSITION_PATH.exists():
        POSITION_PATH.unlink()


def _evaluate_entry(strategy: dict, price_data: dict) -> bool:
    entry = strategy.get("entry", {})
    indicator = entry.get("indicator", "rsi")
    threshold = entry.get("threshold", 30)
    direction = entry.get("direction", "long")

    if indicator == "rsi":
        rsi = price_data.get("rsi")
        if rsi is None:
            return False
        return rsi < threshold if direction == "long" else rsi > threshold
    return False


def _tick_position(strategy: dict, price_data: dict) -> dict | None:
    """Check open position for exit. Returns closed trade record or None."""
    position = _load_position()
    if not position:
        return None

    close = price_data.get("close", 0.0)
    low = price_data.get("low", close)
    rsi = price_data.get("rsi")
    direction = position.get("direction", "long")
    entry_price = position["entry_price"]
    stop_loss_pct = strategy.get("stop_loss_pct", 2.0) / 100

    exit_price = None
    exit_reason = None

    if direction == "long":
        stop_price = entry_price * (1 - stop_loss_pct)
        if low <= stop_price:
            exit_price = stop_price
            exit_reason = "stop_loss"
        elif rsi is not None and rsi > RSI_EXIT_THRESHOLD:
            exit_price = close
            exit_reason = "rsi_exit"

    if exit_price is None:
        return None

    pnl_pct = (exit_price - entry_price) / entry_price
    trade = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "asset": position.get("asset", "SOL/USDT"),
        "strategy_version": strategy.get("version", "01"),
        "direction": direction,
        "entry_price": entry_price,
        "exit_price": round(exit_price, 6),
        "pnl_pct": round(pnl_pct, 6),
        "exit_reason": exit_reason,
        "rsi_at_exit": rsi,
        "closed": True,
    }
    _close_position()
    return trade


def _try_open_position(asset: str, strategy: dict, price_data: dict) -> bool:
    """Open a new position if entry fires and no position is open."""
    if _load_position():
        return False

    if not _evaluate_entry(strategy, price_data):
        return False

    entry_price = price_data.get("close", 0.0)
    if entry_price <= 0:
        return False

    direction = strategy.get("entry", {}).get("direction", "long")
    position = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "asset": asset,
        "direction": direction,
        "entry_price": entry_price,
        "strategy_version": strategy.get("version", "01"),
        "rsi_at_entry": price_data.get("rsi"),
    }
    _save_position(position)
    logger.info("position_open asset=%s direction=%s entry=%.4f rsi=%s",
                asset, direction, entry_price,
                f"{price_data.get('rsi'):.1f}" if price_data.get('rsi') else "n/a")
    return True


def _append_trade(trade: dict) -> None:
    with TRADES_PATH.open("a") as f:
        f.write(json.dumps(trade) + "\n")


def _write_heartbeat(asset: str, consecutive_failures: int, open_position: bool) -> None:
    HEARTBEAT_PATH.write_text(
        json.dumps(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "asset": asset,
                "status": "ok" if consecutive_failures == 0 else "degraded",
                "consecutive_failures": consecutive_failures,
                "open_position": open_position,
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
                    raise RuntimeError("Circuit breaker tripped after 5 consecutive failures")
            else:
                consecutive_failures = 0
                market_data[name] = result

        strategy = _load_strategy()
        price_data = market_data.get("price", {})

        # Check open position for exit
        closed_trade = _tick_position(strategy, price_data)
        if closed_trade:
            _append_trade(closed_trade)
            logger.info("trade_closed asset=%s pnl_pct=%.4f reason=%s",
                        asset, closed_trade["pnl_pct"], closed_trade["exit_reason"])

        # Try to open new position if none open
        _try_open_position(asset, strategy, price_data)

        # Score only closed trades
        trades: list[dict] = []
        if TRADES_PATH.exists():
            for line in TRADES_PATH.read_text().splitlines():
                line = line.strip()
                if line:
                    try:
                        trades.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass

        current_score = score(trades, goal)
        open_pos = _load_position()
        logger.info("tick asset=%s score=%.3f strategy_v=%s trades=%d in_position=%s rsi=%s",
                    asset, current_score, strategy.get("version"), len(trades),
                    bool(open_pos),
                    f"{price_data.get('rsi'):.1f}" if price_data.get('rsi') else "n/a")

        _write_heartbeat(asset, consecutive_failures, bool(open_pos))

        elapsed = time.monotonic() - tick_start
        sleep_for = max(0.0, LOOP_INTERVAL_SECONDS - elapsed)
        await asyncio.sleep(sleep_for)
