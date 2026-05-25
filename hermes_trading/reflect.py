"""Reflection cycle — deterministic fallback (--fallback) and Hermes mode (--hermes)."""
import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import yaml

STATE_DIR = Path(__file__).parent.parent / "state"
STRATEGY_PATH = STATE_DIR / "strategy.yaml"
TRADES_PATH = STATE_DIR / "trades.jsonl"
HYPOTHESES_PATH = STATE_DIR / "hypotheses.jsonl"
HISTORY_DIR = STATE_DIR / "history"
GOAL_PATH = STATE_DIR / "goal.yaml"

HISTORY_DIR.mkdir(parents=True, exist_ok=True)


def _load_trades() -> list[dict]:
    if not TRADES_PATH.exists():
        return []
    return [json.loads(line) for line in TRADES_PATH.read_text().splitlines() if line.strip()]


def _load_strategy() -> dict:
    return yaml.safe_load(STRATEGY_PATH.read_text())


def _load_goal() -> dict:
    return yaml.safe_load(GOAL_PATH.read_text())


def _version_int(strategy: dict) -> int:
    try:
        return int(strategy.get("version", "01"))
    except ValueError:
        return 1


def _save_history(strategy: dict, version_int: int) -> None:
    dest = HISTORY_DIR / f"v{version_int:04d}.yaml"
    dest.write_text(yaml.dump(strategy, default_flow_style=False))


def _bump_version(strategy: dict) -> dict:
    v = _version_int(strategy) + 1
    strategy["version"] = f"{v:02d}"
    return strategy


def _append_hypothesis(hypothesis: dict) -> None:
    with HYPOTHESES_PATH.open("a") as f:
        f.write(json.dumps(hypothesis) + "\n")


def _fallback_reflect(strategy: dict, goal: dict, trades: list[dict]) -> tuple[dict, str]:
    """Deterministic reflection — changes exactly ONE variable."""
    target_return = goal.get("target_return_30d", 0.05)
    max_dd = goal.get("max_drawdown", 0.08)

    if not trades:
        variable_changed = "entry.threshold"
        old_val = strategy["entry"]["threshold"]
        strategy["entry"]["threshold"] = old_val - 2
        reason = f"No trades yet — loosening entry.threshold {old_val} → {strategy['entry']['threshold']} to generate signal"
        return strategy, reason

    pnl_values = [t["pnl_pct"] for t in trades if "pnl_pct" in t]
    total_return = sum(pnl_values)

    # Peak-to-trough drawdown
    cumulative = 0.0
    peak = 0.0
    max_drawdown_realised = 0.0
    for pnl in pnl_values:
        cumulative += pnl
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_drawdown_realised:
            max_drawdown_realised = dd

    if max_drawdown_realised > max_dd:
        old_val = strategy["stop_loss_pct"]
        strategy["stop_loss_pct"] = round(old_val - 0.2, 2)
        reason = (
            f"Drawdown {max_drawdown_realised:.2%} > max {max_dd:.2%} — "
            f"tightening stop_loss_pct {old_val} → {strategy['stop_loss_pct']}"
        )
        variable_changed = "stop_loss_pct"
    else:
        old_val = strategy["entry"]["threshold"]
        strategy["entry"]["threshold"] = old_val - 2
        reason = (
            f"Return {total_return:.2%} < target {target_return:.2%} — "
            f"loosening entry.threshold {old_val} → {strategy['entry']['threshold']}"
        )
        variable_changed = "entry.threshold"

    return strategy, reason


def _hermes_reflect(strategy: dict, goal: dict, trades: list[dict]) -> tuple[dict, str]:
    """Call hermes subprocess to generate and apply a hypothesis."""
    recent_trades = trades[-25:]
    prompt = (
        f"You are the brain of a self-improving trading agent.\n\n"
        f"Current strategy:\n{yaml.dump(strategy)}\n\n"
        f"Goal:\n{yaml.dump(goal)}\n\n"
        f"Recent trades (last {len(recent_trades)}):\n{json.dumps(recent_trades, indent=2)}\n\n"
        "Generate exactly ONE hypothesis: name the single variable in strategy.yaml to change, "
        "the new value, and why. Output ONLY valid JSON: "
        '{"variable": "...", "old_value": ..., "new_value": ..., "reason": "..."}'
    )

    result = subprocess.run(
        ["hermes", "--json"],
        input=prompt,
        capture_output=True,
        text=True,
        timeout=120,
    )

    raw = result.stdout.strip()
    hypothesis = json.loads(raw)

    variable = hypothesis["variable"]
    new_value = hypothesis["new_value"]

    parts = variable.split(".")
    node = strategy
    for part in parts[:-1]:
        node = node[part]
    node[parts[-1]] = new_value

    return strategy, hypothesis.get("reason", "Hermes hypothesis applied")


def main() -> None:
    parser = argparse.ArgumentParser(description="Hermes reflection cycle")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--fallback", action="store_true", help="Deterministic reflection (no Hermes needed)")
    group.add_argument("--hermes", action="store_true", help="Hermes-powered reflection")
    args = parser.parse_args()

    strategy = _load_strategy()
    goal = _load_goal()
    trades = _load_trades()

    current_version = _version_int(strategy)
    _save_history(strategy.copy(), current_version)

    if args.fallback:
        strategy, reason = _fallback_reflect(strategy, goal, trades)
        mode = "fallback"
    else:
        strategy, reason = _hermes_reflect(strategy, goal, trades)
        mode = "hermes"

    strategy = _bump_version(strategy)
    STRATEGY_PATH.write_text(yaml.dump(strategy, default_flow_style=False))

    hypothesis_record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "from_version": f"{current_version:02d}",
        "to_version": strategy["version"],
        "reason": reason,
        "trade_count": len(trades),
    }
    _append_hypothesis(hypothesis_record)

    print(f"✓ Reflection complete ({mode}): v{current_version:02d} → v{strategy['version']}")
    print(f"  Reason: {reason}")


if __name__ == "__main__":
    main()
