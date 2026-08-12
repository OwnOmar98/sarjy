# syntax=docker/dockerfile:1
# Production image for the LiveKit Cloud agent worker (docs/PRD.md,
# deployed via `lk agent deploy` / .github/workflows/agent-deploy.yml).
ARG PYTHON_VERSION=3.11.6
FROM python:${PYTHON_VERSION}-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

ARG UID=10001
RUN adduser \
    --disabled-password \
    --gecos "" \
    --home "/home/appuser" \
    --shell "/sbin/nologin" \
    --uid "${UID}" \
    appuser

# gcc/g++: some deps (e.g. asyncpg) build from source on slim's musl-free
# but toolchain-less base; libglib2.0-0: silero's onnxruntime needs it.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    python3-dev \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

USER appuser
RUN mkdir -p /home/appuser/.cache && chown -R appuser /home/appuser/.cache
WORKDIR /home/appuser

COPY --chown=appuser pyproject.toml .
COPY --chown=appuser *.py .
RUN python -m pip install --user --no-cache-dir .

# Pre-fetch Silero VAD's model at build time so a cold start doesn't
# block on it (prewarm() in main.py loads it per worker process).
RUN python -m livekit.agents download-files

ENTRYPOINT ["python", "main.py"]
CMD ["start"]
