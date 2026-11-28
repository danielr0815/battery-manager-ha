#!/usr/bin/env python3
"""Validate checked-out release metadata; never rewrite a branch or tag."""

import argparse
import json
import tomllib
from pathlib import Path


def validate(root: Path, tag: str | None = None) -> str:
    root = root.resolve()
    manifest_path = root / "custom_components/battery_manager/manifest.json"
    project_path = root / "pyproject.toml"
    for path in (manifest_path, project_path):
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError(f"Release metadata is outside the checkout: {path.name}")
    manifest = json.loads(manifest_path.read_text())
    project = tomllib.loads(project_path.read_text())
    version = manifest["version"]
    if project["project"]["version"] != version:
        raise ValueError("Manifest and project versions differ")
    if tag is not None and tag != f"v{version}":
        raise ValueError(f"Tag {tag!r} does not match checked-out version {version!r}")
    return version


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag")
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    args = parser.parse_args()
    print(validate(args.root, args.tag))
