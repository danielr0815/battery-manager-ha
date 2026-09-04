FROM mcr.microsoft.com/devcontainers/python:3.14

# Playwright MCP uses the distribution Chromium instead of downloading a
# version-coupled browser on every container rebuild. Headless mode avoids
# leaking host display/authentication sockets into the development container.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends chromium \
    && rm -rf /var/lib/apt/lists/*
