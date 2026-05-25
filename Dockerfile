FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && rm -rf /var/lib/apt/lists/*
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"
COPY pyproject.toml ./
COPY hermes_trading ./hermes_trading
# Bake defaults into image — entrypoint seeds /app/state on first boot
COPY state/goal.yaml state/strategy.yaml /app/defaults/
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
RUN uv sync
ENV HERMES_TRADING_MODE=paper
ENV HERMES_STATE_DIR=/app/state
ENTRYPOINT ["/entrypoint.sh"]
CMD ["uv", "run", "python", "-m", "hermes_trading.run"]
