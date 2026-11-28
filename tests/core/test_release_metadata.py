"""Release gates must validate the checked-out version, never repair metadata."""

import json
import subprocess
import sys
from pathlib import Path

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


def test_trusted_release_script_reads_candidate_metadata_as_data(tmp_path):
    manifest = tmp_path / "custom_components/battery_manager/manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"version": "0.70.0"}))
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.70.0"\n')
    candidate = tmp_path / "scripts/check_release.py"
    candidate.parent.mkdir()
    candidate.write_text('raise RuntimeError("candidate code must not run")')
    trusted = Path(__file__).resolve().parents[2] / "scripts/check_release.py"

    result = subprocess.run(
        [sys.executable, str(trusted), "--root", str(tmp_path), "--tag", "v0.70.0"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "0.70.0"


def test_release_metadata_cannot_escape_candidate_checkout(tmp_path):
    real_manifest = tmp_path / "manifest.json"
    real_manifest.write_text(json.dumps({"version": "0.70.0"}))
    root = tmp_path / "candidate"
    manifest = root / "custom_components/battery_manager/manifest.json"
    manifest.parent.mkdir(parents=True)
    try:
        manifest.symlink_to(real_manifest)
    except OSError:
        pytest.skip("Creating symlinks is unavailable")
    (root / "pyproject.toml").write_text('[project]\nversion = "0.70.0"\n')
    with pytest.raises(ValueError, match="outside the checkout"):
        validate(root, "v0.70.0")
