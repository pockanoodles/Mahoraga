# Cloud agent base image for Mahoraga (Python 3.12 + build tools for hnswlib).
# Do not COPY the project here — Cursor checks out the repo separately.
FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
