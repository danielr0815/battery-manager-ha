#!/usr/bin/env python3
"""Validate checked-out release metadata; never rewrite a branch or tag."""

import argparse
import json
import tomllib
from pathlib import Path


def validate(root: Path, tag: str | None = None) -> str:
    manifest = json.loads(
        (root / "custom_components/battery_manager/manifest.json").read_text()
    )
    project = tomllib.loads((root / "pyproject.toml").read_text())
    version = manifest["version"]
    if project["project"]["version"] != version:
        raise ValueError("Manifest and project versions differ")
    if tag is not None and tag != f"v{version}":
        raise ValueError(f"Tag {tag!r} does not match checked-out version {version!r}")
    return version


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag")
    args = parser.parse_args()
    print(validate(Path(__file__).resolve().parents[1], args.tag))
