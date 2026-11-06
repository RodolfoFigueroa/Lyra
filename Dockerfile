FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        git \
        libgdal-dev \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock .python-version ./

# Keep third-party dependencies cached when Lyra source changes.
RUN uv sync --frozen --no-dev --no-cache --no-install-project --extra server

COPY src/lyra ./src/lyra
COPY LICENSE ./LICENSE

RUN uv sync --frozen --no-dev --no-cache --no-editable --extra server \
    && find /app/.venv -type d -name "__pycache__" -prune -exec rm -rf {} + \
    && find /app/.venv -type f -name "*.py[co]" -delete \
    && rm -rf /root/.cache/uv

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/app/.venv

ENV PATH="$VIRTUAL_ENV/bin:$PATH"

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/.venv /app/.venv
COPY LICENSE ./LICENSE

# Durable Lyra app files and deployment-provided secrets live under /lyra_data.
RUN mkdir -p \
        /lyra_data/config \
        /lyra_data/secrets \
        /lyra_data/cache/jobs \
        /lyra_data/logs
VOLUME /lyra_data

EXPOSE 5219

CMD ["python", "-m", "lyra.server.main"]
