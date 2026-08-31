"""Fixtures for Home Assistant layer tests (require Linux/WSL or CI)."""

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading custom integrations in all HA tests."""
    yield


@pytest.fixture(autouse=True)
def immediate_coordinator_debounce(monkeypatch):
    """Run event-driven refreshes without the production five-second wait.

    ``hass.async_block_till_done()`` deliberately waits for background tasks.
    Leaving the real debounce active therefore charged five seconds to every
    test that changed a tracked entity, even though those tests verify the
    refresh result rather than wall-clock passage.  The production delay has a
    dedicated mock-based contract test in ``test_coordinator.py``.
    """
    monkeypatch.setattr(
        "custom_components.battery_manager.coordinator.DEBOUNCE_SECONDS",
        0,
    )
