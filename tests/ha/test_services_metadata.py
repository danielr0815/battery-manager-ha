"""services.yaml and services-translation structure tests.

Pins the services.yaml documentation (name/description per service,
documented fields, the config_entry selector for entry_id) and the de/en
symmetry of the services and learning-issues translation sections. Pure
file checks — no running HA needed.
"""

import json
from pathlib import Path

import yaml
from homeassistant.helpers.selector import validate_selector

INTEGRATION_DIR = Path(__file__).parents[2] / "custom_components" / "battery_manager"
SERVICE_FIELDS = {
    "export_learned_profiles": ("entry_id", "file_path", "download", "as_table"),
    "export_hourly_details": ("entry_id", "file_path", "download", "as_table"),
    "test_cascade_terminal": ("entry_id", "cascade_id"),
}
LEARNING_ISSUES = ("learning_recorder_timeout", "learning_no_recorder")


def _load_services_yaml():
    with (INTEGRATION_DIR / "services.yaml").open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _load_translations(lang):
    path = INTEGRATION_DIR / "translations" / f"{lang}.json"
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def test_services_yaml_documents_every_service():
    """Every registered service has name/description and documents all
    fields of the voluptuous schema in __init__.py."""
    services = _load_services_yaml()
    assert sorted(services) == sorted(SERVICE_FIELDS)
    for name, fields in SERVICE_FIELDS.items():
        service = services[name]
        assert service["name"]
        assert service["description"]
        assert sorted(service["fields"]) == sorted(fields)


def test_services_yaml_entry_id_uses_config_entry_selector():
    """entry_id uses the config_entry selector (available in the pinned HA
    version — validate_selector proves the config schema is accepted) so
    the UI offers a Battery Manager entry picker."""
    services = _load_services_yaml()
    for name in SERVICE_FIELDS:
        entry_id = services[name]["fields"]["entry_id"]
        assert entry_id["selector"] == {
            "config_entry": {"integration": "battery_manager"}
        }
        validate_selector(entry_id["selector"])  # raises on an unknown selector
        assert entry_id.get("advanced") is True


def test_services_translations_symmetric():
    """de/en carry identical services key sets, each with non-empty
    name/description per service and field (HA convention:
    services.<service>.name/description/fields.<field>.*)."""
    de = _load_translations("de")["services"]
    en = _load_translations("en")["services"]
    assert sorted(de) == sorted(en) == sorted(SERVICE_FIELDS)
    for lang in (de, en):
        for name, fields in SERVICE_FIELDS.items():
            service = lang[name]
            assert service["name"]
            assert service["description"]
            assert sorted(service["fields"]) == sorted(fields)
            for field in fields:
                assert service["fields"][field]["name"]
                assert service["fields"][field]["description"]


def test_learning_issue_translations_symmetric():
    """The learner's repair issues (history_profile.py) have title and
    description in both languages."""
    for lang in ("de", "en"):
        issues = _load_translations(lang)["issues"]
        for key in LEARNING_ISSUES:
            assert issues[key]["title"]
            assert issues[key]["description"]
