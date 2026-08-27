FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# Override on networks that block files.pythonhosted.org:
#   podman build --build-arg UV_DEFAULT_INDEX=https://<mirror>/simple .
ARG UV_DEFAULT_INDEX=https://pypi.org/simple

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_DEFAULT_INDEX=${UV_DEFAULT_INDEX} \
    PATH="/app/.venv/bin:$PATH" \
    HOST=0.0.0.0 \
    PORT=2009 \
    DB_PATH=/data/playground.db

WORKDIR /app

RUN useradd --create-home --shell /usr/sbin/nologin app \
    && mkdir -p /data \
    && chown -R app:app /app /data

USER app

COPY --chown=app:app pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY --chown=app:app . .

EXPOSE 2009

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:2009/health', timeout=3).read()" || exit 1

CMD ["python", "main.py"]
