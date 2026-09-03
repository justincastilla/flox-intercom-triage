FROM python:3.12-slim

# git: shallow-clones the source repos at boot and refreshes them hourly.
# ripgrep: the retrieval tool the agent searches with.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ripgrep ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY requirements.txt .
RUN uv pip install --system --no-cache -r requirements.txt

COPY app ./app

# Repos are re-cloned on boot (~2s, 27MB), so no volume is needed; durable state
# lives in Postgres. Writable for the non-root user.
ENV REPO_DIR=/app/repos \
    GAP_REPORT_PATH=/app/repos/doc-gaps.md \
    PYTHONUNBUFFERED=1
RUN useradd --create-home --uid 10001 triage && mkdir -p /app/repos && chown -R triage /app
USER triage

EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
