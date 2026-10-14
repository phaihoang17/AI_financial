# Sandbox worker image for M7_SANDBOX_ISOLATION=container-v1 (ADR-072).
# Same Linux worker on Linux, macOS (Docker Desktop / Podman) and Windows
# (Docker Desktop / Podman). Run it only by digest:
#   docker build -f deploy/sandbox/container/Dockerfile -t ai-financial-sandbox:v1 .
#   docker image inspect --format '{{index .RepoDigests 0}}' ai-financial-sandbox:v1   # after push
#   export SANDBOX_CONTAINER_IMAGE=<registry>/ai-financial-sandbox@sha256:<digest>
# Pin the base image by digest in your registry mirror before production builds.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
# The workers import only the standard library, NumPy and pandas (pinned as in
# requirements-test.txt); nothing else is installed.
RUN pip install numpy==2.4.6 pandas==2.3.3 \
    && rm -rf /root/.cache

# Code is baked in read-only; the container root is mounted read-only at run time.
COPY src /opt/ai-financial/src
RUN find /opt/ai-financial -type d -exec chmod 0555 {} + \
    && find /opt/ai-financial -type f -exec chmod 0444 {} +

USER 65534:65534
WORKDIR /tmp
# No ENTRYPOINT: the parent passes `/usr/bin/env -i <exact env> python -m <worker>`.
