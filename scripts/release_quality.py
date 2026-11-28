#!/usr/bin/env python3
"""Find successful GitHub Actions quality checks for an exact release target."""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from typing import TypeGuard
from urllib.parse import quote

POLL_SECONDS = 30
PUSH_GRACE_SECONDS = 60
PUSH_TIMEOUT_SECONDS = 40 * 60
ACTIVE_STATUSES = ("requested", "waiting", "pending", "queued", "in_progress")
RELEASE_WORKFLOW = ".github/workflows/release.yml"
QUALITY_WITNESS = "Fresh quality validated"
WORKFLOW_CONTRACT = (
    RELEASE_WORKFLOW,
    ".github/workflows/validate.yml",
    "scripts/release_quality.py",
    "scripts/check_release.py",
)


class QualityError(Exception):
    """GitHub checks failed or their evidence could not be established."""


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _api(endpoint: str, *, paginate: bool = False) -> object:
    command = ["gh", "api", "--method", "GET"]
    if paginate:
        command.append("--paginate")
    try:
        result = subprocess.run(
            [*command, endpoint], capture_output=True, check=True, timeout=20
        )
    except subprocess.CalledProcessError as exc:
        raise QualityError(
            f"GitHub request failed for {endpoint} (gh exit code {exc.returncode})"
        ) from exc
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise QualityError(f"Cannot read GitHub data for {endpoint}: {exc}") from exc

    try:
        if not paginate:
            return json.loads(result.stdout)

        # Without --slurp, gh emits one JSON document per page.
        remaining = result.stdout.decode()
        pages = []
        decoder = json.JSONDecoder()
        while remaining := remaining.lstrip():
            page, end = decoder.raw_decode(remaining)
            pages.append(page)
            remaining = remaining[end:]
        return pages
    except ValueError as exc:
        raise QualityError(f"Invalid GitHub JSON for {endpoint}") from exc


def _list_records(endpoint: str, key: str) -> list[object]:
    pages = _api(endpoint, paginate=True)
    if not isinstance(pages, list) or not pages:
        raise QualityError(f"Missing GitHub pages for {endpoint}")
    records = []
    for page in pages:
        if not isinstance(page, dict) or not isinstance(page.get(key), list):
            raise QualityError(f"Invalid GitHub {key} listing for {endpoint}")
        records.extend(page[key])
    return records


def _repository_name(value: object) -> str | None:
    if isinstance(value, dict) and isinstance(name := value.get("full_name"), str):
        return name.casefold()
    return None


def _positive_int(value: object) -> TypeGuard[int]:
    return type(value) is int and value > 0


def _matching_push_runs(repository: str, commit: str) -> list[dict]:
    endpoint = (
        f"repos/{repository}/actions/workflows/validate.yml/runs"
        f"?event=push&head_sha={commit}&per_page=100"
    )
    expected_repo = repository.casefold()
    matches = []
    for run in _list_records(endpoint, "workflow_runs"):
        if not isinstance(run, dict):
            continue
        if run.get("head_sha") != commit or run.get("event") != "push":
            continue
        run_repo = _repository_name(run.get("repository"))
        head_repo = _repository_name(run.get("head_repository"))
        if run_repo not in (None, expected_repo) or head_repo not in (
            None,
            expected_repo,
        ):
            continue
        if run_repo is None or head_repo is None or not _positive_int(run.get("id")):
            raise QualityError(
                "Incomplete repository or run ID for exact push Validate"
            )
        matches.append(run)
    return matches


def _wait_for_push(repository: str, commit: str) -> int | None:
    start = time.monotonic()
    deadline = start + PUSH_GRACE_SECONDS
    observed_active = False
    while True:
        runs = _matching_push_runs(repository, commit)
        active = []
        failures = []
        for run in runs:
            run_id = run["id"]
            status = run.get("status")
            if status == "completed":
                conclusion = run.get("conclusion")
                if conclusion == "success":
                    return run_id
                if not isinstance(conclusion, str) or not conclusion:
                    raise QualityError(f"Push Validate run {run_id} has no conclusion")
                failures.append(f"{run_id} ({conclusion})")
            elif status in ACTIVE_STATUSES:
                active.append(run_id)
            else:
                raise QualityError(f"Push Validate run {run_id} has unknown status")
        if failures and not active:
            raise QualityError(f"Push Validate did not succeed: {', '.join(failures)}")
        if active and not observed_active:
            observed_active = True
            deadline = start + PUSH_TIMEOUT_SECONDS
            _log(f"Waiting for push Validate run(s) {', '.join(map(str, active))}.")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            if observed_active:
                raise QualityError("Timed out waiting for exact push Validate")
            return None
        time.sleep(min(POLL_SECONDS, remaining))


def _is_git_sha(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) is not None


def _commit_sha(value: object) -> str | None:
    if isinstance(value, dict) and _is_git_sha(sha := value.get("sha")):
        return sha
    return None


def _default_head(repository: str, branch: str) -> str:
    ref = _api(f"repos/{repository}/git/ref/heads/{quote(branch, safe='')}")
    if isinstance(ref, dict) and ref.get("ref") == f"refs/heads/{branch}":
        obj = ref.get("object")
        if (
            isinstance(obj, dict)
            and obj.get("type") == "commit"
            and (sha := _commit_sha(obj)) is not None
        ):
            return sha
    raise QualityError("GitHub did not return the default branch commit")


def _in_default_history(repository: str, source: str, head: str) -> bool:
    if source == head:
        return True

    # Pin both ends so a concurrent push cannot change the comparison.
    comparison = _api(f"repos/{repository}/compare/{source}...{head}?per_page=1")
    if not isinstance(comparison, dict):
        raise QualityError("Invalid GitHub default branch comparison")
    status = comparison.get("status")
    merge_base = _commit_sha(comparison.get("merge_base_commit"))
    if (
        status not in ("ahead", "identical", "behind", "diverged")
        or _commit_sha(comparison.get("base_commit")) != source
        or merge_base is None
    ):
        raise QualityError("Invalid GitHub default branch comparison")
    if status in ("behind", "diverged"):
        return False
    if merge_base != source:
        raise QualityError("Inconsistent GitHub default branch comparison")
    return True


def _workflow_contract(repository: str, source: str) -> dict[str, str]:
    commit = _api(f"repos/{repository}/git/commits/{source}")
    if (
        not isinstance(commit, dict)
        or _commit_sha(commit) != source
        or (tree_sha := _commit_sha(commit.get("tree"))) is None
    ):
        raise QualityError(f"Invalid GitHub workflow commit {source}")

    tree = _api(f"repos/{repository}/git/trees/{tree_sha}?recursive=1")
    if (
        not isinstance(tree, dict)
        or _commit_sha(tree) != tree_sha
        or tree.get("truncated") is not False
        or not isinstance(tree.get("tree"), list)
    ):
        raise QualityError(f"Invalid or truncated GitHub workflow tree {tree_sha}")

    contract = {}
    seen = set()
    for entry in tree["tree"]:
        if not isinstance(entry, dict) or not isinstance(
            name := entry.get("path"), str
        ):
            raise QualityError(f"Invalid entry in GitHub workflow tree {tree_sha}")
        if name not in WORKFLOW_CONTRACT:
            continue
        if name in seen or (sha := _commit_sha(entry)) is None:
            raise QualityError(
                f"Invalid contract entry {name} in workflow tree {tree_sha}"
            )
        seen.add(name)
        if entry.get("type") == "blob" and entry.get("mode") in ("100644", "100755"):
            contract[name] = sha
    return contract


def _has_fresh_quality_job(
    repository: str, run_id: int, attempt: int, source: str
) -> bool:
    endpoint = (
        f"repos/{repository}/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100"
    )
    for job in _list_records(endpoint, "jobs"):
        if (
            isinstance(job, dict)
            and job.get("name") == QUALITY_WITNESS
            and job.get("status") == "completed"
            and job.get("conclusion") == "success"
            and _positive_int(job.get("run_id"))
            and job["run_id"] == run_id
            and job.get("head_sha") == source
        ):
            return True
    return False


def _reuse_prepared_release(
    repository: str, default_branch: str, tag: str, commit: str, workflow_source: str
) -> int | None:
    title = f"Prepare release {tag} {commit}"
    endpoint = (
        f"repos/{repository}/actions/workflows/release.yml/runs"
        f"?event=workflow_dispatch&status=success&branch={quote(default_branch, safe='')}"
        "&per_page=100"
    )
    expected_repo = repository.casefold()
    workflow_paths = (
        RELEASE_WORKFLOW,
        f"{RELEASE_WORKFLOW}@{default_branch}",
        f"{RELEASE_WORKFLOW}@refs/heads/{default_branch}",
    )
    head = None
    contract_matches: dict[str, bool] = {}
    current_contract = None
    for run in _list_records(endpoint, "workflow_runs"):
        if not isinstance(run, dict) or run.get("display_title") != title:
            continue
        if (
            _repository_name(run.get("repository")) != expected_repo
            or _repository_name(run.get("head_repository")) != expected_repo
            or run.get("path") not in workflow_paths
            or run.get("event") != "workflow_dispatch"
            or run.get("head_branch") != default_branch
            or run.get("status") != "completed"
            or run.get("conclusion") != "success"
        ):
            continue
        run_id = run.get("id")
        attempt = run.get("run_attempt")
        source = run.get("head_sha")
        if not (
            _positive_int(run_id) and _positive_int(attempt) and _is_git_sha(source)
        ):
            raise QualityError(
                "Incomplete GitHub run data for exact release preparation"
            )

        if head is None:
            head = _default_head(repository, default_branch)
        if source not in contract_matches:
            contract_matches[source] = _in_default_history(repository, source, head)
            if contract_matches[source] and source != workflow_source:
                if current_contract is None:
                    current_contract = _workflow_contract(repository, workflow_source)
                    if len(current_contract) != len(WORKFLOW_CONTRACT):
                        raise QualityError(
                            "Current release workflow contract is incomplete"
                        )
                contract_matches[source] = (
                    _workflow_contract(repository, source) == current_contract
                )

        if contract_matches[source] and _has_fresh_quality_job(
            repository, run_id, attempt, source
        ):
            return run_id
    return None


def find_quality_run(
    repository: str,
    default_branch: str,
    commit: str,
    quality_blob: str,
    target_quality_blob: str,
    *,
    tag: str,
    workflow_source: str,
) -> int | None:
    if target_quality_blob == quality_blob:
        if (run_id := _wait_for_push(repository, commit)) is not None:
            return run_id
    else:
        _log("Target Validate workflow differs from the release quality workflow.")
    return _reuse_prepared_release(
        repository, default_branch, tag, commit, workflow_source
    )


def _git_sha(value: str) -> str:
    if not _is_git_sha(value):
        raise argparse.ArgumentTypeError("expected a full lowercase Git SHA")
    return value


def _optional_git_sha(value: str) -> str:
    return _git_sha(value) if value else value


def _repository(value: str) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9-]+/[a-zA-Z0-9_.-]+", value):
        raise argparse.ArgumentTypeError("expected a GitHub repository as OWNER/REPO")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True, type=_repository)
    parser.add_argument("--default-branch", required=True)
    parser.add_argument("--commit", required=True, type=_git_sha)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--workflow-source", required=True, type=_git_sha)
    parser.add_argument("--quality-blob", required=True, type=_git_sha)
    parser.add_argument("--target-quality-blob", required=True, type=_optional_git_sha)
    args = parser.parse_args()
    try:
        if not os.environ.get("GH_TOKEN"):
            raise QualityError("GH_TOKEN is required to read GitHub Actions data")
        run_id = find_quality_run(
            args.repository,
            args.default_branch,
            args.commit,
            args.quality_blob,
            args.target_quality_blob,
            tag=args.tag,
            workflow_source=args.workflow_source,
        )
    except QualityError as exc:
        _log(str(exc))
        return 1
    if run_id is not None:
        _log(f"Reusing successful quality checks from GitHub Actions run {run_id}.")
        print(run_id)
    else:
        _log("No reusable quality result found. Release quality checks will run.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
