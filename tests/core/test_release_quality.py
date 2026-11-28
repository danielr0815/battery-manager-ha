"""Release reuse requires successful checks for the exact target and workflow."""

import json
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from scripts import release_quality

REPOSITORY = "owner/battery-manager"
BRANCH = "main"
COMMIT = "a" * 40
QUALITY_BLOB = "b" * 40
OTHER_SHA = "c" * 40
FORK_SHA = "d" * 40
DEFAULT_HEAD = "e" * 40
CURRENT_TREE = "1" * 40
HISTORICAL_TREE = "2" * 40
TAG = "v0.63.0"
RELEASE_TITLE = f"Prepare release {TAG} {COMMIT}"
REPOSITORY_DATA = {"full_name": REPOSITORY}
PUSH_ENDPOINT = (
    f"repos/{REPOSITORY}/actions/workflows/validate.yml/runs"
    f"?event=push&head_sha={COMMIT}&per_page=100"
)
RELEASE_RUNS_ENDPOINT = (
    f"repos/{REPOSITORY}/actions/workflows/release.yml/runs"
    "?event=workflow_dispatch&status=success&branch=main&per_page=100"
)
DEFAULT_HEAD_ENDPOINT = f"repos/{REPOSITORY}/git/ref/heads/{BRANCH}"


def _push_run(**changes):
    return {
        "id": 101,
        "head_sha": COMMIT,
        "event": "push",
        "repository": REPOSITORY_DATA,
        "head_repository": REPOSITORY_DATA,
        "status": "completed",
        "conclusion": "success",
    } | changes


def _release_run(run_id, **changes):
    return {
        "id": run_id,
        "head_sha": OTHER_SHA,
        "display_title": RELEASE_TITLE,
        "run_attempt": 2,
        "head_branch": BRANCH,
        "path": ".github/workflows/release.yml@refs/heads/main",
        "event": "workflow_dispatch",
        "repository": REPOSITORY_DATA,
        "head_repository": REPOSITORY_DATA,
        "status": "completed",
        "conclusion": "success",
    } | changes


def _witness(run_id=91, *, source=OTHER_SHA, **changes):
    return {
        "name": "Fresh quality validated",
        "status": "completed",
        "conclusion": "success",
        "run_id": run_id,
        "head_sha": source,
    } | changes


def _commit_tree(commit, tree):
    return {"sha": commit, "tree": {"sha": tree}}


def _tree(tree_id, *, changed=None, truncated=False):
    paths = (
        ".github/workflows/release.yml",
        ".github/workflows/validate.yml",
        "scripts/check_release.py",
        "scripts/release_quality.py",
    )
    entries = [
        {
            "path": path,
            "sha": ("f" if path == changed else str(index)) * 40,
            "mode": "100644",
            "type": "blob",
        }
        for index, path in enumerate(paths, 1)
    ]
    return {"sha": tree_id, "truncated": truncated, "tree": entries}


def _head(sha=OTHER_SHA, *, ref=f"refs/heads/{BRANCH}"):
    return {"ref": ref, "object": {"type": "commit", "sha": sha}}


def _comparison(status, source, merge_base):
    return {
        "status": status,
        "base_commit": {"sha": source},
        "merge_base_commit": {"sha": merge_base},
    }


def _page(key, *records):
    return [{key: list(records)}]


@pytest.fixture
def github_cli(monkeypatch):
    def fake_gh(*responses):
        outcomes = iter(responses)

        def result(command, **_kwargs):
            payload = next(outcomes)
            if isinstance(payload, Exception):
                raise payload
            if "--paginate" in command and isinstance(payload, list):
                stdout = "".join(json.dumps(page) for page in payload)
            else:
                stdout = json.dumps(payload)
            return subprocess.CompletedProcess(command, 0, stdout=stdout.encode())

        run = Mock(side_effect=result)
        monkeypatch.setattr(release_quality.subprocess, "run", run)
        return run

    return fake_gh


@pytest.fixture
def clock(monkeypatch):
    now = 0

    def monotonic():
        return now

    def sleep(seconds):
        nonlocal now
        now += seconds

    sleep_mock = Mock(side_effect=sleep)
    monkeypatch.setattr(
        release_quality, "time", SimpleNamespace(monotonic=monotonic, sleep=sleep_mock)
    )
    return sleep_mock


@pytest.fixture
def cli(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "example-token")

    def invoke(
        target_blob=QUALITY_BLOB, *, default_branch=BRANCH, workflow_source=OTHER_SHA
    ):
        monkeypatch.setattr(
            release_quality.sys,
            "argv",
            [
                "release_quality.py",
                "--repository",
                REPOSITORY,
                "--default-branch",
                default_branch,
                "--commit",
                COMMIT,
                "--tag",
                TAG,
                "--workflow-source",
                workflow_source,
                "--quality-blob",
                QUALITY_BLOB,
                "--target-quality-blob",
                target_blob,
            ],
        )
        return release_quality.main()

    return invoke


def _request(endpoint, *, paginated=True):
    args = ["gh", "api", "--method", "GET"]
    if paginated:
        args.append("--paginate")
    return call([*args, endpoint], capture_output=True, check=True, timeout=20)


def test_uses_successful_exact_push_and_ignores_unrelated_runs(
    cli, github_cli, clock, capsys
):
    gh = github_cli(
        _page(
            "workflow_runs",
            None,
            {"head_sha": OTHER_SHA},
            _push_run(id=12, head_sha=OTHER_SHA, conclusion="failure"),
            _push_run(id=13, event="pull_request", conclusion="failure"),
            _push_run(id=14, repository={"full_name": "fork/battery-manager"}),
            _push_run(id=15, head_repository={"full_name": "fork/battery-manager"}),
        )
        + _page(
            "workflow_runs", _push_run(repository={"full_name": REPOSITORY.upper()})
        )
    )

    assert cli() == 0
    captured = capsys.readouterr()
    assert captured.out == "101\n"
    assert "run 101" in captured.err
    assert gh.call_args_list == [_request(PUSH_ENDPOINT)]
    clock.assert_not_called()


def test_waits_for_push_to_appear_and_finish(cli, github_cli, clock, capsys):
    github_cli(
        _page("workflow_runs"),
        _page("workflow_runs", _push_run(status="queued", conclusion=None)),
        _page("workflow_runs", _push_run(status="in_progress", conclusion=None)),
        _page("workflow_runs", _push_run()),
    )

    assert cli() == 0
    captured = capsys.readouterr()
    assert captured.out == "101\n"
    assert "Waiting for push Validate" in captured.err
    assert clock.call_args_list == [call(30), call(30), call(30)]


def test_falls_back_after_short_grace_when_no_reusable_run_exists(
    cli, github_cli, clock, capsys
):
    gh = github_cli(
        *[_page("workflow_runs")] * 3,
        _page("workflow_runs"),
    )

    assert cli() == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Release quality checks will run" in captured.err
    assert clock.call_args_list == [call(30), call(30)]
    assert gh.call_args_list == [_request(PUSH_ENDPOINT)] * 3 + [
        _request(RELEASE_RUNS_ENDPOINT)
    ]


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "skipped"])
def test_matching_unsuccessful_push_blocks_fallback(
    conclusion, cli, github_cli, clock, capsys
):
    gh = github_cli(_page("workflow_runs", _push_run(conclusion=conclusion)))

    assert cli() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert f"101 ({conclusion})" in captured.err
    assert gh.call_args_list == [_request(PUSH_ENDPOINT)]
    clock.assert_not_called()


@pytest.mark.parametrize("disappears", [False, True])
def test_pending_push_timeout_blocks_fallback(
    disappears, cli, github_cli, clock, capsys
):
    pending = _page("workflow_runs", _push_run(status="pending", conclusion=None))
    later = _page("workflow_runs") if disappears else pending
    gh = github_cli(pending, *[later] * 80)

    assert cli() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Timed out" in captured.err
    assert gh.call_args_list == [_request(PUSH_ENDPOINT)] * 81
    assert clock.call_args_list == [call(30)] * 80


def _attempt_endpoint(run_id, attempt=2):
    return (
        f"repos/{REPOSITORY}/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100"
    )


@pytest.mark.parametrize("path_suffix", ["", "@main", "@refs/heads/main"])
def test_reuses_successful_preparation_from_same_workflow_source(
    path_suffix, cli, github_cli, clock, capsys
):
    gh = github_cli(
        *[_page("workflow_runs")] * 3,
        _page(
            "workflow_runs",
            _release_run(91, path=f".github/workflows/release.yml{path_suffix}"),
        ),
        _head(),
        _page("jobs", _witness()),
    )

    assert cli() == 0
    assert capsys.readouterr().out == "91\n"
    assert clock.call_args_list == [call(30), call(30)]
    assert gh.call_args_list == [_request(PUSH_ENDPOINT)] * 3 + [
        _request(RELEASE_RUNS_ENDPOINT),
        _request(DEFAULT_HEAD_ENDPOINT, paginated=False),
        _request(_attempt_endpoint(91)),
    ]


@pytest.mark.parametrize("target_blob", [OTHER_SHA, ""])
def test_gate_mismatch_skips_push_and_filters_release_preparations(
    target_blob, cli, github_cli, clock, capsys
):
    gh = github_cli(
        _page(
            "workflow_runs",
            None,
            _release_run(80, display_title=f"Prepare release {TAG} {FORK_SHA}"),
            _release_run(81, display_title=f"Prepare release v0.62.0 {COMMIT}"),
            _release_run(82, display_title=f"{RELEASE_TITLE} forged suffix"),
            _release_run(91, path=".github/workflows/unrelated.yml@main"),
            _release_run(92, conclusion="failure"),
            _release_run(93, event="release"),
            _release_run(94, head_branch="another-branch"),
            _release_run(95, repository={"full_name": "fork/battery-manager"}),
            _release_run(96, path=".github/workflows/release.yml@refs/tags/main"),
            _release_run(97),
            _release_run(98, run_attempt=3),
        ),
        _head(),
        _page(
            "jobs",
            _witness(97, conclusion="skipped"),
            _witness(97, name="Other / Fresh quality validated"),
            _witness(97, source=COMMIT),
            _witness(96),
        ),
        _page("jobs", _witness(98)),
    )

    assert cli(target_blob) == 0
    captured = capsys.readouterr()
    assert captured.out == "98\n"
    assert "workflow differs" in captured.err
    assert gh.call_args_list == [
        _request(RELEASE_RUNS_ENDPOINT),
        _request(DEFAULT_HEAD_ENDPOINT, paginated=False),
        _request(_attempt_endpoint(97)),
        _request(_attempt_endpoint(98, attempt=3)),
    ]
    clock.assert_not_called()


def test_reuses_ancestor_with_identical_contract_and_checks_pinned_history(
    cli, github_cli, capsys
):
    branch = "stable/current"
    bare_path = ".github/workflows/release.yml"
    gh = github_cli(
        _page(
            "workflow_runs",
            _release_run(90, head_sha=FORK_SHA, head_branch=branch, path=bare_path),
            _release_run(91, head_branch=branch, path=bare_path),
        ),
        _head(DEFAULT_HEAD, ref=f"refs/heads/{branch}"),
        _comparison("diverged", FORK_SHA, COMMIT),
        _comparison("ahead", OTHER_SHA, OTHER_SHA),
        _commit_tree(DEFAULT_HEAD, CURRENT_TREE),
        _tree(CURRENT_TREE),
        _commit_tree(OTHER_SHA, HISTORICAL_TREE),
        _tree(HISTORICAL_TREE),
        _page("jobs", _witness()),
    )

    assert cli(OTHER_SHA, default_branch=branch, workflow_source=DEFAULT_HEAD) == 0
    assert capsys.readouterr().out == "91\n"
    assert gh.call_args_list == [
        _request(
            RELEASE_RUNS_ENDPOINT.replace("branch=main", "branch=stable%2Fcurrent")
        ),
        _request(f"repos/{REPOSITORY}/git/ref/heads/stable%2Fcurrent", paginated=False),
        _request(
            f"repos/{REPOSITORY}/compare/{FORK_SHA}...{DEFAULT_HEAD}?per_page=1",
            paginated=False,
        ),
        _request(
            f"repos/{REPOSITORY}/compare/{OTHER_SHA}...{DEFAULT_HEAD}?per_page=1",
            paginated=False,
        ),
        _request(f"repos/{REPOSITORY}/git/commits/{DEFAULT_HEAD}", paginated=False),
        _request(
            f"repos/{REPOSITORY}/git/trees/{CURRENT_TREE}?recursive=1", paginated=False
        ),
        _request(f"repos/{REPOSITORY}/git/commits/{OTHER_SHA}", paginated=False),
        _request(
            f"repos/{REPOSITORY}/git/trees/{HISTORICAL_TREE}?recursive=1",
            paginated=False,
        ),
        _request(_attempt_endpoint(91)),
    ]


@pytest.mark.parametrize(
    "changed_path",
    [
        ".github/workflows/release.yml",
        ".github/workflows/validate.yml",
        "scripts/check_release.py",
        "scripts/release_quality.py",
    ],
)
def test_different_historical_contract_requires_fresh_checks(
    changed_path, cli, github_cli, capsys
):
    github_cli(
        _page("workflow_runs", _release_run(91)),
        _head(DEFAULT_HEAD),
        _comparison("ahead", OTHER_SHA, OTHER_SHA),
        _commit_tree(DEFAULT_HEAD, CURRENT_TREE),
        _tree(CURRENT_TREE),
        _commit_tree(OTHER_SHA, HISTORICAL_TREE),
        _tree(HISTORICAL_TREE, changed=changed_path),
    )

    assert cli(OTHER_SHA, workflow_source=DEFAULT_HEAD) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Release quality checks will run" in captured.err


@pytest.mark.parametrize(
    "responses,error",
    [
        ([_head(ref="refs/tags/main")], "did not return the default branch commit"),
        (
            [_head(DEFAULT_HEAD), subprocess.CalledProcessError(1, "gh")],
            "GitHub request failed",
        ),
        (
            [_head(DEFAULT_HEAD), _comparison("ahead", OTHER_SHA, COMMIT)],
            "Inconsistent GitHub default branch comparison",
        ),
        (
            [
                _head(DEFAULT_HEAD),
                _comparison("ahead", OTHER_SHA, OTHER_SHA),
                _commit_tree(DEFAULT_HEAD, CURRENT_TREE),
                _tree(CURRENT_TREE, truncated=True),
            ],
            "Invalid or truncated GitHub workflow tree",
        ),
    ],
)
def test_invalid_release_evidence_blocks_fallback(
    responses, error, cli, github_cli, capsys
):
    github_cli(_page("workflow_runs", _release_run(91)), *responses)

    assert cli(OTHER_SHA, workflow_source=DEFAULT_HEAD) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert error in captured.err


@pytest.mark.parametrize(
    "response,error",
    [
        (subprocess.CalledProcessError(1, "gh"), "GitHub request failed"),
        ([], "Missing GitHub pages"),
        ([{"workflow_runs": None}], "Invalid GitHub workflow_runs listing"),
        (_page("workflow_runs", _push_run(id=True)), "Incomplete repository or run ID"),
        (_page("workflow_runs", _push_run(conclusion=None)), "has no conclusion"),
        (_page("workflow_runs", _push_run(status=None)), "has unknown status"),
    ],
)
def test_untrustworthy_push_data_blocks_fallback(
    response, error, cli, github_cli, clock, capsys
):
    gh = github_cli(response)

    assert cli() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert error in captured.err
    assert gh.call_args_list == [_request(PUSH_ENDPOINT)]
    clock.assert_not_called()


def test_missing_run_attempt_is_api_error(cli, github_cli, capsys):
    github_cli(_page("workflow_runs", _release_run(91, run_attempt=None)))

    assert cli(OTHER_SHA) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Incomplete GitHub run data" in captured.err


def test_missing_fresh_quality_witness_requires_fresh_checks(cli, github_cli, capsys):
    github_cli(
        _page("workflow_runs", _release_run(91)),
        _head(),
        _page("jobs", _witness(conclusion="skipped")),
    )

    assert cli(OTHER_SHA) == 0
    assert capsys.readouterr().out == ""


def test_cli_requires_actions_token(cli, github_cli, monkeypatch, capsys):
    gh = github_cli()
    monkeypatch.delenv("GH_TOKEN")

    assert cli() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "GH_TOKEN is required" in captured.err
    gh.assert_not_called()
