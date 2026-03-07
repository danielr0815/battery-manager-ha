# Security Policy

## Supported Versions

We take security seriously and actively maintain the following versions:

| Version | Supported          | End of Life |
| ------- | ------------------ | ----------- |
| 0.1.x   | :white_check_mark: | TBD         |

## Security Considerations

### Deployment Environment

Battery Manager is designed to run as a Home Assistant custom integration within a trusted local network. The following security considerations apply:

1. **Local Network Only**: This integration should only be exposed within your trusted home network
2. **Home Assistant Authentication**: Access control is managed by Home Assistant's authentication system
3. **No Cloud Dependencies**: All processing happens locally, no data is sent to external services

### Known Security Measures

#### Path Traversal Protection
- File export service validates all file paths to prevent directory traversal attacks
- All paths are normalized and validated against allowed base directories
- Null byte injection protection is implemented

#### Input Validation
- Entry IDs are validated to be alphanumeric (plus hyphens and underscores)
- All numeric inputs are validated against acceptable ranges
- Configuration parameters are validated using voluptuous schemas

#### Data Privacy
- No sensitive data is logged in production mode
- All file operations use explicit UTF-8 encoding
- Generated files are restricted to the Home Assistant configuration directory

### Security Best Practices for Users

1. **Keep Home Assistant Updated**: Ensure you're running a supported version of Home Assistant
2. **Network Isolation**: Do not expose your Home Assistant instance directly to the internet without proper security measures (VPN, Nabu Casa, etc.)
3. **Regular Backups**: Maintain regular backups of your Home Assistant configuration
4. **Review Logs**: Periodically review logs for any suspicious activity
5. **Principle of Least Privilege**: Only grant necessary permissions to users

## Reporting a Vulnerability

We appreciate your efforts to responsibly disclose security vulnerabilities.

### Where to Report

**Please DO NOT report security vulnerabilities through public GitHub issues.**

Instead, please report security vulnerabilities by emailing: **security@battery-manager.local** (placeholder - update with actual email)

Alternatively, you can:
- Use GitHub's private security advisory feature: https://github.com/danielr0815/battery-manager-ha/security/advisories/new
- Contact the maintainers directly through GitHub private messages

### What to Include

Please include the following information in your report:

1. **Type of vulnerability** (e.g., injection, authentication bypass, data exposure)
2. **Affected versions** (if known)
3. **Steps to reproduce** the vulnerability
4. **Potential impact** of the vulnerability
5. **Suggested fix** (if you have one)
6. **Your contact information** for follow-up

### Response Timeline

- **Initial Response**: Within 48 hours of receiving your report
- **Status Update**: Within 7 days with our assessment and planned actions
- **Fix Timeline**: Critical vulnerabilities will be addressed within 30 days
- **Public Disclosure**: We follow coordinated disclosure practices and will work with you on appropriate timing

### What to Expect

1. **Acknowledgment**: We'll confirm receipt of your vulnerability report
2. **Validation**: We'll validate the vulnerability and assess its severity
3. **Fix Development**: We'll develop and test a fix
4. **Security Advisory**: We'll publish a security advisory (if appropriate)
5. **Credit**: We'll acknowledge your contribution (unless you prefer to remain anonymous)

### Severity Classification

We use the Common Vulnerability Scoring System (CVSS) v3.1:

- **Critical (9.0-10.0)**: Immediate threat, patch within 7 days
- **High (7.0-8.9)**: Significant threat, patch within 30 days
- **Medium (4.0-6.9)**: Moderate threat, patch within 90 days
- **Low (0.1-3.9)**: Minor threat, patch in next regular release

### Bug Bounty Program

We currently do not offer a bug bounty program. However, we deeply appreciate security research and will publicly acknowledge contributors (with their permission).

## Security Audits

### Latest Security Review

- **Date**: 2026-03-07
- **Scope**: Full codebase review focusing on:
  - Input validation
  - Path traversal vulnerabilities
  - Injection vulnerabilities
  - Data exposure risks
  - Authentication/authorization issues

### Known Security Limitations

1. **No Built-in Rate Limiting**: Service calls are not rate-limited by default (rely on Home Assistant's built-in protections)
2. **Local File System Access**: The export service writes to the local file system (restricted to config directory)
3. **No Encryption at Rest**: Configuration data is stored in plaintext (standard for Home Assistant integrations)

## Security Development Practices

### Code Review
- All code changes undergo peer review
- Security-sensitive changes require additional review
- Automated security scanning in CI/CD pipeline

### Dependency Management
- Minimal external dependencies (only Home Assistant core)
- Regular updates for security patches
- Dependabot alerts enabled

### Testing
- Unit tests for security-critical functions
- Integration tests for input validation
- Fuzzing for parser and validation logic

### Continuous Integration
- Automated security scanning (Bandit, Safety)
- Code quality checks (flake8, mypy)
- Dependency vulnerability scanning

## Compliance

### Data Protection
- **GDPR Compliance**: No personal data is transmitted externally
- **Data Retention**: Historical data is retained only as configured by user
- **Right to Erasure**: Uninstalling the integration removes all data

### Home Assistant Standards
- Follows Home Assistant integration quality checklist
- Complies with HACS (Home Assistant Community Store) requirements
- Adheres to Home Assistant's security best practices

## Contact

For security-related questions that don't involve vulnerabilities:
- Open a GitHub Discussion: https://github.com/danielr0815/battery-manager-ha/discussions
- Check existing documentation: [README.md](README.md)

## Changelog

### Version 0.1.0 (2026-03-07)
- Initial security policy
- Path traversal protection implemented
- Input validation hardening
- Security audit completed

---

**Last Updated**: 2026-03-07
**Policy Version**: 1.0
