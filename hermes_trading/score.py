"""Score a list of closed trades against a goal definition.

Returns a float in [-1, +1].
  +1  = all three metrics at or above target
  -1  = all three metrics at or below failure_below floor
   0  = mixed / insufficient data
"""
import math
from typing import Any


def score(trades: list[dict[str, Any]], goal: dict[str, Any]) -> float:
    if not trades:
        return 0.0

    pnl_values = [t["pnl_pct"] for t in trades if "pnl_pct" in t]
    if not pnl_values:
        return 0.0

    target_return = goal.get("target_return_30d", 0.05)
    max_dd = goal.get("max_drawdown", 0.08)
    min_sharpe = goal.get("min_sharpe", 1.2)
    failure_below = goal.get("failure_below", -0.04)

    # --- realised return sub-score ---
    total_return = sum(pnl_values)
    if total_return >= target_return:
        return_score = 1.0
    elif total_return <= failure_below:
        return_score = -1.0
    elif total_return < 0:
        return_score = total_return / abs(failure_below)
    else:
        return_score = total_return / target_return

    # --- drawdown sub-score ---
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

    if max_drawdown_realised <= 0:
        drawdown_score = 1.0
    elif max_drawdown_realised >= max_dd:
        drawdown_score = -1.0
    else:
        drawdown_score = 1.0 - (max_drawdown_realised / max_dd)

    # --- Sharpe sub-score ---
    if len(pnl_values) < 2:
        sharpe_score = 0.0
    else:
        mean_ret = sum(pnl_values) / len(pnl_values)
        variance = sum((r - mean_ret) ** 2 for r in pnl_values) / (len(pnl_values) - 1)
        std_ret = math.sqrt(variance) if variance > 0 else 0.0
        sharpe = mean_ret / std_ret if std_ret > 0 else 0.0
        if sharpe >= min_sharpe:
            sharpe_score = 1.0
        elif sharpe <= 0:
            sharpe_score = -1.0
        else:
            sharpe_score = (sharpe / min_sharpe) * 2.0 - 1.0

    composite = (return_score + drawdown_score + sharpe_score) / 3.0
    return max(-1.0, min(1.0, composite))
