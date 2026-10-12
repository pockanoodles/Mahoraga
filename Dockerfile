# syntax=docker/dockerfile:1

# The Mahoraga engine and its web UI in one image. Ollama and your coding agents
# stay on the host: the container serves the API and the report, it does not
# run models, and it cannot see your repos unless you mount them.
#
#   docker run --rm -p 127.0.0.1:8000:8000 \
#     -v mahoraga-data:/home/mahoraga/.mahoraga-v2 \
#     ghcr.io/pockanoodles/mahoraga
#
# Publish the port on 127.0.0.1 as above. The server listens on every interface
# inside the container and has no login, so a bare `-p 8000:8000` would expose
# it to your network.
#
# To look at audits you ran on the host, mount their folder (read-only is fine):
#   -v ~/.mahoraga-v2/agentbench:/home/mahoraga/.mahoraga-v2/agentbench:ro
#
# Ollama on the host is read from OLLAMA_BASE_URL (set below to the Docker
# Desktop alias). On Linux, add `--add-host=host.docker.internal:host-gateway`
# and start Ollama with OLLAMA_HOST=0.0.0.0 so the container can reach it.
#
# The entrypoint is the CLI itself, so any command works:
#   docker run --rm ghcr.io/pockanoodles/mahoraga version

# 1. Web UI. The output is plain files, so build it once on the host's own
#    architecture rather than under emulation for each target platform. The
#    frontend build copies the brand marks from ../brand, so that comes along.
FROM --platform=$BUILDPLATFORM node:22-slim AS ui
WORKDIR /src
COPY brand brand
COPY frontend frontend
COPY scripts/build_ui.sh scripts/build_ui.sh
RUN mkdir -p backend/orchestrator/service && bash scripts/build_ui.sh

# 2. Python package and its dependencies, built into a virtualenv. hnswlib has no
#    wheels and compiles here. Its build uses -march=native unless told not to,
#    which would tie the image to the CPU that built it and crash on others.
FROM python:3.12-slim AS build
# Unpinned on purpose: this stage is thrown away, and a pinned version stops
# existing at the next Debian point release.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
ENV HNSWLIB_NO_NATIVE=1
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY backend backend
COPY --from=ui /src/backend/orchestrator/service/ui backend/orchestrator/service/ui
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir .

# 3. Runtime: no compiler, no source tree, no root.
FROM python:3.12-slim
LABEL org.opencontainers.image.title="Mahoraga" \
      org.opencontainers.image.description="Is local coding AI worth it for your code? Measure it, then route it." \
      org.opencontainers.image.source="https://github.com/pockanoodles/Mahoraga" \
      org.opencontainers.image.licenses="MIT"
RUN useradd --create-home --uid 1000 mahoraga \
    && install -d -o mahoraga -g mahoraga /home/mahoraga/.mahoraga-v2
COPY --from=build /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    OLLAMA_BASE_URL=http://host.docker.internal:11434
USER 1000:1000
WORKDIR /home/mahoraga
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=8)"]
ENTRYPOINT ["mahoraga"]
CMD ["serve", "--host", "0.0.0.0"]
