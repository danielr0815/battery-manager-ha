"""Fixtures for Home Assistant layer tests (require Linux/WSL or CI)."""

import pytest


@pytest.fixture(autouse=True)
def isolated_operation_archive(monkeypatch, tmp_path):
    """Archive files belong to one test, including across repeated pytest runs.

    HA's mocked JSON Store does not intercept our separate chunk files. Use a
    real temporary directory to exercise fsync/rename without sharing the
    test helper's installation directory between coordinators and workers.
    """
    import hashlib

    monkeypatch.setattr(
        "custom_components.battery_manager.archive_storage.archive_directory",
        lambda _hass, entry_id: (
            tmp_path / hashlib.sha256(entry_id.encode()).hexdigest()
        ),
    )


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


@pytest.fixture(autouse=True)
def settle_initial_plans(monkeypatch):
    """Existing behavior tests await the initial plan when settling HA work.

    Entry setup now intentionally returns before planning. Tests that inspect
    that intermediate state explicitly pass wait_background_tasks=False; all
    other tests wait for the actual entry-owned initial task, not a time delay.
    """
    import asyncio

    from homeassistant.core import HomeAssistant

    original = HomeAssistant.async_block_till_done

    async def settle(hass, wait_background_tasks=None):
        if wait_background_tasks is None:
            for coordinator in list(hass.data.get("battery_manager", {}).values()):
                task = getattr(coordinator, "_initial_refresh_task", None)
                if task is not None and not task.done():
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        if not task.cancelled():
                            raise
        await original(hass, wait_background_tasks=bool(wait_background_tasks))

    monkeypatch.setattr(HomeAssistant, "async_block_till_done", settle)
