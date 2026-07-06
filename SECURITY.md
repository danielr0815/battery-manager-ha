# Security Policy

Battery Manager is a community-maintained Home Assistant custom integration. It
runs entirely locally, ships no external Python dependencies, and makes no cloud
calls. This policy is scoped to what a small open-source project can realistically
promise.

## Supported versions

Only the latest release (tracked on the `main` branch, installed by HACS) is
supported. Please reproduce any report against the current version before filing.

## Reporting a vulnerability

**Please do not report security issues in public GitHub issues.**

Report privately via **[GitHub Security Advisories](https://github.com/danielr0815/battery-manager-ha/security/advisories/new)**.
Please include: affected version, steps to reproduce, and the impact you see.

As a single-maintainer project there is no guaranteed SLA, but reports are taken
seriously and acknowledged as soon as practical. Once a fix is available it is
merged to `main` (which HACS tracks) and, if warranted, published as a GitHub
Security Advisory. Reporters are credited unless they prefer to remain anonymous.

## Security-relevant design

- **Local only** — the integration is meant for a trusted local network; access
  control is Home Assistant's. No data leaves the instance.
- **Export path containment** — the `export_hourly_details` service resolves the
  target path and rejects anything outside the Home Assistant config directory
  (and `/local/`), and rejects null bytes in the filename.
- **Input validation** — config and service inputs are validated with
  voluptuous schemas; numeric ranges and cross-field constraints are enforced in
  the config flow.
- **No secrets at rest beyond HA norms** — configuration is stored by Home
  Assistant like any other integration; nothing extra is persisted in plaintext.

## What this project does *not* have

To set honest expectations, there is currently **no** bug-bounty program, no
formal CVSS SLA, and no Bandit/Safety/fuzzing security pipeline. CI runs ruff,
Home Assistant `hassfest`, HACS validation, and the test suite. Dependabot keeps
the GitHub Actions current.

## User best practices

- Keep Home Assistant updated and do not expose it directly to the internet
  (use a VPN or Nabu Casa).
- Keep regular configuration backups.
- Uninstalling the integration removes its stored data.
