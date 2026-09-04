#!/usr/bin/env bash
set -euo pipefail

# The Codex IDE extension starts stdio MCP servers in the environment that owns
# the workspace. Inside the devcontainer, use its Chromium; on the host, retain
# the existing visible-browser wrapper and its logged-in browser profile.
if command -v chromium >/dev/null 2>&1; then
  exec playwright-mcp \
    --browser chromium \
    --executable-path "$(command -v chromium)" \
    --headless \
    --ignore-https-errors \
    --no-sandbox \
    --user-data-dir .playwright-mcp/browser-profile \
    "$@"
fi

readonly HOST_WRAPPER="${HOME}/.local/bin/playwright-mcp-visible"
if [[ -x "${HOST_WRAPPER}" ]]; then
  exec "${HOST_WRAPPER}" "$@"
fi

echo "Playwright MCP: neither container Chromium nor ${HOST_WRAPPER} is available" >&2
exit 1
