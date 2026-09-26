"""Release gates must validate the checked-out version, never repair metadata."""

import json

import pytest

from scripts.check_release import validate


@pytest.mark.parametrize("tag", [None, "v0.46.0"])
def test_matching_release_metadata_is_accepted_without_writes(tmp_path, tag):
    manifest = tmp_path / "custom_components/battery_manager/manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"version": "0.46.0"}))
    project = tmp_path / "pyproject.toml"
    project.write_text('[project]\nversion = "0.46.0"\n')
    before = manifest.read_bytes(), project.read_bytes()
    assert validate(tmp_path, tag) == "0.46.0"
    assert (manifest.read_bytes(), project.read_bytes()) == before


@pytest.mark.parametrize(
    "project_version,tag", [("0.45.1", "v0.46.0"), ("0.46.0", "v0.45.1")]
)
def test_mismatched_release_metadata_is_rejected(tmp_path, project_version, tag):
    manifest = tmp_path / "custom_components/battery_manager/manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"version": "0.46.0"}))
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nversion = "{project_version}"\n'
    )
    with pytest.raises(ValueError, match="versions differ|does not match"):
        validate(tmp_path, tag)
