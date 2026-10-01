# The guest image for a `web`-mode sandboxed share session (docs/SHARE_SANDBOX.md).
#
# python:3.12-slim (the default sandbox image) plus a headless Chromium driven by
# Playwright, curl, and the tiny `browse` CLI the guest's Claude uses to read a page.
# Nothing here is a credential or a host path: the Claude binary and the workspace are
# bind-mounted at run time, read-only and read-write respectively, and every byte of
# network traffic leaves through the session's filtering egress proxy (HTTPS_PROXY).
#
# Build (from anywhere; the path is printed by `chela share-session --web` when missing):
#   docker build -t chela-share-web <this directory>
FROM python:3.12-slim

ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates git \
 && pip install "playwright>=1.45,<2" \
 && playwright install --with-deps chromium \
 && chmod -R a+rX /opt/playwright \
 && rm -rf /var/lib/apt/lists/*

COPY browse.py /usr/local/bin/browse
RUN chmod 755 /usr/local/bin/browse
