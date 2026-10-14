# The copilot's own service: the screens, the API and the scheduled check-ins.
# Built and started by `docker compose up` alongside Postgres, Zammad and Mailpit.
FROM python:3.12.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

RUN pip install --no-cache-dir uv==0.11.32 \
    && useradd --create-home --uid 10001 copilot

WORKDIR /app

# Dependencies first, so code changes do not reinstall them.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project

COPY src ./src
COPY datagen ./datagen
COPY scripts ./scripts
# The demo screens read the dev split. The held-out test split stays out of the image.
COPY data/dev ./data/dev
COPY reports ./reports
RUN uv sync --locked --no-dev && chown -R copilot /app/reports

USER copilot
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/settings', timeout=4)"]
CMD ["/app/.venv/bin/uvicorn", "copilot.web.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
