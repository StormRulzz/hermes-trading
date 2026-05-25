"""Entry point for the Hermes trading worker."""
import argparse
import asyncio
import logging
import yaml
from pathlib import Path

from hermes_trading.loop import run_loop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

GOAL_PATH = Path(__file__).parent.parent / "state" / "goal.yaml"


def main() -> None:
    goal = yaml.safe_load(GOAL_PATH.read_text())

    parser = argparse.ArgumentParser(description="Hermes trading worker")
    parser.add_argument("--asset", default=goal.get("asset", "SOL/USDT"))
    args = parser.parse_args()

    logger.info("Booting hermes-trading worker — asset=%s mode=paper", args.asset)
    asyncio.run(run_loop(asset=args.asset, goal=goal))


if __name__ == "__main__":
    main()
