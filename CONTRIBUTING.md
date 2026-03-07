# Contributing to Battery Manager

Thank you for your interest in contributing to Battery Manager! This document provides guidelines and instructions for contributing to this project.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [How to Contribute](#how-to-contribute)
- [Coding Standards](#coding-standards)
- [Testing Guidelines](#testing-guidelines)
- [Pull Request Process](#pull-request-process)
- [Issue Guidelines](#issue-guidelines)
- [Community](#community)

## Code of Conduct

This project adheres to the Contributor Covenant [Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code. Please report unacceptable behavior to the project maintainers.

## Getting Started

### Prerequisites

- Python 3.11 or higher
- Home Assistant 2024.1.0 or higher
- Git for version control
- A Home Assistant development environment (optional but recommended)

### Understanding the Project

Before contributing, familiarize yourself with:
- [README.md](README.md) - Project overview and usage
- [ARCHITECTURE.md](ARCHITECTURE.md) - Technical architecture and design
- [CHANGELOG.md](CHANGELOG.md) - Version history and changes

## Development Setup

### 1. Fork and Clone

```bash
# Fork the repository on GitHub, then clone your fork
git clone https://github.com/YOUR_USERNAME/battery-manager-ha.git
cd battery-manager-ha

# Add upstream remote
git remote add upstream https://github.com/danielr0815/battery-manager-ha.git
```

### 2. Create Development Environment

```bash
# Create virtual environment
python -m venv venv

# Activate virtual environment
# On Linux/Mac:
source venv/bin/activate
# On Windows:
venv\Scripts\activate

# Install development dependencies
pip install -r requirements-dev.txt
```

### 3. Install Pre-commit Hooks

```bash
# Install pre-commit hooks
pre-commit install

# Test the hooks
pre-commit run --all-files
```

### 4. Set Up Home Assistant Development Environment

For integration testing:

```bash
# Create a development Home Assistant configuration
mkdir -p ~/.homeassistant-dev/custom_components
ln -s $(pwd)/custom_components/battery_manager ~/.homeassistant-dev/custom_components/

# Run Home Assistant in development mode
hass -c ~/.homeassistant-dev --debug
```

## How to Contribute

### Reporting Bugs

Before creating bug reports, please check existing issues. When creating a bug report, include:

- **Clear title**: Brief summary of the issue
- **Description**: Detailed description of the problem
- **Steps to reproduce**: Numbered steps to reproduce the behavior
- **Expected behavior**: What you expected to happen
- **Actual behavior**: What actually happened
- **Environment**:
  - Home Assistant version
  - Battery Manager version
  - Python version
  - Operating system
- **Logs**: Relevant log entries from Home Assistant
- **Configuration**: Your battery manager configuration (sanitized)

Use the bug report template: [.github/ISSUE_TEMPLATE/bug_report.md](.github/ISSUE_TEMPLATE/bug_report.md)

### Suggesting Features

Feature requests are welcome! Please include:

- **Use case**: Why is this feature needed?
- **Proposed solution**: How should it work?
- **Alternatives**: Other solutions you've considered
- **Additional context**: Any other relevant information

Use the feature request template: [.github/ISSUE_TEMPLATE/feature_request.md](.github/ISSUE_TEMPLATE/feature_request.md)

### Contributing Code

1. **Find or create an issue**: Discuss the change before starting work
2. **Create a branch**: Use descriptive branch names
   ```bash
   git checkout -b feature/your-feature-name
   # or
   git checkout -b fix/issue-number-description
   ```
3. **Make your changes**: Follow coding standards
4. **Test your changes**: Run tests and linting
5. **Commit your changes**: Use clear commit messages
6. **Push to your fork**: `git push origin your-branch-name`
7. **Create a Pull Request**: Use the PR template

## Coding Standards

### Python Style Guide

We follow [PEP 8](https://pep8.org/) with some modifications:

- **Line length**: Maximum 88 characters (Black default)
- **Indentation**: 4 spaces (no tabs)
- **Quotes**: Double quotes for strings
- **Imports**: Organized using isort

### Code Formatting

We use automated formatters:

```bash
# Format code with Black
black custom_components/battery_manager

# Sort imports with isort
isort custom_components/battery_manager

# Check for linting issues
flake8 custom_components/battery_manager

# Type checking
mypy custom_components/battery_manager
```

### Type Hints

- Use type hints for all function signatures
- Use `from __future__ import annotations` for forward references
- Prefer specific types over `Any`
- Use `TypedDict` or `dataclasses` for complex data structures

**Good:**
```python
def calculate_threshold(soc: float, capacity: int) -> float:
    """Calculate SOC threshold."""
    return soc * capacity / 100
```

**Bad:**
```python
def calculate_threshold(soc, capacity):
    return soc * capacity / 100
```

### Documentation

- All modules must have docstrings
- All public functions/classes must have docstrings
- Use Google-style docstrings:

```python
def complex_function(param1: str, param2: int) -> bool:
    """Brief description of the function.

    Longer description explaining the function's behavior,
    algorithm, and any important details.

    Args:
        param1: Description of param1
        param2: Description of param2

    Returns:
        Description of return value

    Raises:
        ValueError: When param2 is negative
    """
    if param2 < 0:
        raise ValueError("param2 must be non-negative")
    return param1 and param2 > 0
```

### Naming Conventions

- **Modules**: `lowercase_with_underscores.py`
- **Classes**: `PascalCase`
- **Functions**: `lowercase_with_underscores()`
- **Constants**: `UPPERCASE_WITH_UNDERSCORES`
- **Private**: Prefix with single underscore `_private_function()`

## Testing Guidelines

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=custom_components/battery_manager --cov-report=html

# Run specific test file
pytest tests/test_battery.py

# Run with verbose output
pytest -v
```

### Writing Tests

- Write tests for all new functionality
- Aim for 80%+ code coverage
- Test edge cases and error conditions
- Use meaningful test names

**Example:**
```python
def test_battery_charge_within_limits():
    """Test that battery charging respects max SOC limit."""
    battery = Battery(capacity_wh=5000, max_soc_percent=95.0)
    battery.charge(1000)  # Attempt to charge beyond limit
    assert battery.get_soc() <= 95.0
```

### Test Organization

```
tests/
├── __init__.py
├── conftest.py              # Shared fixtures
├── test_battery.py          # Battery module tests
├── test_controller.py       # Controller tests
├── test_integration.py      # Integration tests
└── fixtures/
    └── test_data.json       # Test data files
```

## Pull Request Process

### Before Submitting

1. **Update documentation**: README, docstrings, etc.
2. **Run tests**: `pytest`
3. **Run linters**: `pre-commit run --all-files`
4. **Update CHANGELOG.md**: Add entry under "Unreleased"
5. **Rebase on main**: `git rebase upstream/main`

### PR Title Format

Use conventional commits format:

- `feat: Add new feature`
- `fix: Fix bug in controller`
- `docs: Update README`
- `test: Add battery tests`
- `refactor: Simplify energy flow logic`
- `chore: Update dependencies`
- `ci: Update GitHub Actions`

### PR Description

Include:
- **Summary**: What does this PR do?
- **Motivation**: Why is this change needed?
- **Testing**: How was this tested?
- **Screenshots**: For UI changes
- **Breaking changes**: Any breaking changes?
- **Closes**: Reference related issues (e.g., "Closes #123")

### Review Process

1. **Automated checks**: CI/CD must pass
2. **Code review**: At least one maintainer approval required
3. **Testing**: Reviewer may test changes locally
4. **Feedback**: Address review comments
5. **Merge**: Maintainer will merge when approved

### After Merge

- Delete your branch (done automatically)
- Update your fork: `git pull upstream main`
- Close related issues (if not auto-closed)

## Issue Guidelines

### Issue Labels

- `bug`: Something isn't working
- `enhancement`: New feature or request
- `documentation`: Improvements to documentation
- `good first issue`: Good for newcomers
- `help wanted`: Extra attention needed
- `question`: Further information requested
- `security`: Security-related issue
- `wontfix`: This will not be worked on

### Issue Assignment

- Comment on the issue to express interest
- Wait for maintainer assignment
- Start work only after assignment
- Update the issue with progress

## Commit Message Guidelines

### Format

```
<type>(<scope>): <subject>

<body>

<footer>
```

**Example:**
```
feat(controller): Add support for variable efficiency

Implement dynamic efficiency calculation based on load.
This improves accuracy for partial load scenarios.

Closes #45
```

### Types

- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation changes
- `style`: Code style changes (formatting)
- `refactor`: Code refactoring
- `test`: Adding or updating tests
- `chore`: Maintenance tasks
- `ci`: CI/CD changes

## Code Review Checklist

Reviewers should verify:

- [ ] Code follows style guidelines
- [ ] Tests are included and passing
- [ ] Documentation is updated
- [ ] No security vulnerabilities introduced
- [ ] Breaking changes are documented
- [ ] Commit messages are clear
- [ ] PR description is complete

## Community

### Getting Help

- **GitHub Discussions**: For questions and discussions
- **GitHub Issues**: For bugs and feature requests
- **Documentation**: Check docs first

### Staying Updated

- Watch the repository for notifications
- Read CHANGELOG.md for updates
- Follow commit activity

## Recognition

Contributors are recognized in:
- CHANGELOG.md for significant contributions
- GitHub contributors page
- Release notes for major features

## License

By contributing, you agree that your contributions will be licensed under the same [MIT License](LICENSE) that covers this project.

---

Thank you for contributing to Battery Manager! Your efforts help make home energy management better for everyone.
