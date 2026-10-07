"""External collection continues through a Core outage; all delays are mocked."""

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "custom_components/battery_manager/tools/capture_ha_memory.sh"
)


@pytest.fixture
def terminal(tmp_path):
    binary = tmp_path / "bin"
    binary.mkdir()
    scripts = {
        "ha": """#!/bin/sh
echo "$*" >> "$CAPTURE_TEST_ROOT/commands"
if [ "$1 $2" = "core stats" ] && [ ! -f "$CAPTURE_TEST_ROOT/retried" ]; then
    touch "$CAPTURE_TEST_ROOT/retried"
    echo 'Core unavailable'
    exit 1
fi
echo '{"result":"ok","data":{"memory_usage":123}}'
""",
        "timeout": """#!/bin/sh
echo "$1" >> "$CAPTURE_TEST_ROOT/timeouts"
shift
exec "$@"
""",
        "sleep": """#!/bin/sh
echo "$1" >> "$CAPTURE_TEST_ROOT/sleeps"
""",
        "date": """#!/bin/sh
if [ "$1" = '+%s' ]; then echo 100; else echo 2026-10-07T14:31:20Z; fi
""",
    }
    for name, body in scripts.items():
        target = binary / name
        target.write_text(body)
        target.chmod(0o700)
    environment = dict(
        os.environ, PATH=f"{binary}:/usr/bin:/bin", CAPTURE_TEST_ROOT=str(tmp_path)
    )
    return tmp_path, environment


def run(terminal, *args):
    return subprocess.run(
        ["/bin/sh", str(SCRIPT), *map(str, args)],
        env=terminal[1],
        cwd=terminal[0],
        capture_output=True,
        text=True,
        check=False,
    )


def test_external_recorder_survives_core_failure_and_samples_selected_addons(terminal):
    root = terminal[0]
    output = root / "memory.log"
    result = run(terminal, output, 3, 2, "core_mariadb")
    assert result.returncode == 0, result.stderr
    evidence = output.read_text()
    assert "request_failed exit=1" in evidence
    assert evidence.count("host_memory sample=") == 3
    assert evidence.count('"memory_usage":123') == 9
    assert "MemAvailable:" in evidence and "SwapFree:" in evidence
    assert evidence.endswith("capture_complete\n")
    commands = (root / "commands").read_text().splitlines()
    assert commands.count("addons stats core_mariadb --raw-json") == 3
    assert all("stats" in line or "core info" in line for line in commands)
    assert set((root / "timeouts").read_text().splitlines()) == {"3"}
    assert (root / "sleeps").read_text().splitlines() == ["2", "2"]
    assert output.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["", 1, 2],
        ["log", 0, 2],
        ["log", 901, 2],
        ["log", "bad", 2],
        ["log", 1, 0],
        ["log", 1, 61],
        ["log", 1, "bad"],
        ["log", 1, 2, "--restart"],
        ["log", 1, 2, "$(touch bad)"],
    ],
)
def test_invalid_arguments_never_issue_commands(terminal, args):
    assert run(terminal, *args).returncode == 2
    assert not (terminal[0] / "commands").exists()


def test_existing_evidence_and_symlink_targets_are_preserved(terminal):
    root = terminal[0]
    existing = root / "existing"
    existing.write_text("evidence")
    link = root / "link"
    link.symlink_to(existing)
    for target in (existing, link):
        assert run(terminal, target, 1, 2).returncode == 2
    assert existing.read_text() == "evidence"
    assert not (root / "commands").exists()


def test_missing_cli_is_reported_without_creating_an_output(terminal):
    root, env = terminal
    env["PATH"] = str(root / "empty")
    output = root / "memory.log"
    result = run(terminal, output, 1, 2)
    assert result.returncode == 2
    assert "Missing command: ha" in result.stderr
    assert not output.exists()
