#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_BASE_URL = "https://sentry.io"
DEFAULT_ORG = "suu-hb"
DEFAULT_NOTE_DIR = ".codex/sentry-fix"

ISSUE_URL_RE = re.compile(r"Issue URL:\s+(https?://[^\s`]+/issues/(\d+)/?)")


def run_command(
    args: list[str],
    *,
    cwd: Path,
    capture_output: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        cwd=cwd,
        text=True,
        capture_output=capture_output,
    )
    if check and result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else result.stdout.strip()
        raise RuntimeError(
            f"Command failed ({result.returncode}): {' '.join(args)}\n{stderr}"
        )
    return result


def default_commit_range() -> str:
    event_name = os.environ.get("GITHUB_EVENT_NAME")
    before = os.environ.get("GITHUB_EVENT_BEFORE", "")
    sha = os.environ.get("GITHUB_SHA", "")

    if event_name == "push" and before and sha and any(ch != "0" for ch in before):
        return f"{before}..{sha}"

    return "HEAD~1..HEAD"


def note_files_for_commit_range(
    repo_root: Path, commit_range: str, note_dir: Path
) -> list[Path]:
    result = run_command(
        [
            "git",
            "diff",
            "--name-only",
            "--diff-filter=AM",
            commit_range,
            "--",
            str(note_dir),
        ],
        cwd=repo_root,
        capture_output=True,
    )
    note_files: list[Path] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if line:
            note_files.append(repo_root / line)
    return note_files


def extract_issue_id(note_text: str) -> str | None:
    match = ISSUE_URL_RE.search(note_text)
    if not match:
        return None
    return match.group(2)


def collect_issue_ids(note_files: list[Path]) -> list[str]:
    issue_ids: list[str] = []
    seen: set[str] = set()
    for note_file in note_files:
        if not note_file.exists():
            continue
        issue_id = extract_issue_id(note_file.read_text(encoding="utf-8"))
        if not issue_id or issue_id in seen:
            continue
        seen.add(issue_id)
        issue_ids.append(issue_id)
    return issue_ids


def request_json(
    url: str,
    token: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    retries: int = 1,
) -> Any:
    payload = None if body is None else json.dumps(body).encode("utf-8")
    req = Request(url, data=payload, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/json")
    if body is not None:
        req.add_header("Content-Type", "application/json")

    attempt = 0
    while True:
        try:
            with urlopen(req) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else None
        except HTTPError as err:
            raw = err.read().decode("utf-8", "ignore")
            if attempt < retries and (err.code >= 500 or err.code == 429):
                attempt += 1
                continue
            raise RuntimeError(
                f"HTTP {err.code} for {url}: {raw or 'request failed'}"
            ) from err
        except URLError as err:
            if attempt < retries:
                attempt += 1
                continue
            raise RuntimeError(f"Network error for {url}: {err.reason}") from err


def resolve_issue(base_url: str, org: str, token: str, issue_id: str) -> None:
    path = f"/api/0/organizations/{org}/issues/{issue_id}/"
    url = f"{base_url.rstrip('/')}{path}"
    request_json(url, token, method="PUT", body={"status": "resolved"})


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Resolve Sentry issues referenced by sentry-fix note files."
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("SENTRY_BASE_URL", DEFAULT_BASE_URL),
        help="Sentry base URL",
    )
    parser.add_argument(
        "--org",
        default=os.environ.get("SENTRY_ORG", DEFAULT_ORG),
        help="Sentry org slug",
    )
    parser.add_argument(
        "--note-dir",
        default=os.environ.get("SENTRY_NOTE_DIR", DEFAULT_NOTE_DIR),
        help="Directory that contains sentry-fix note files",
    )
    parser.add_argument(
        "--repo-root",
        default=os.environ.get("SENTRY_REPO_ROOT") or os.environ.get("GITHUB_WORKSPACE") or os.getcwd(),
        help="Repository root used for git diff and note lookup",
    )
    parser.add_argument(
        "--commit-range",
        default=os.environ.get("SENTRY_RESOLVE_COMMIT_RANGE", ""),
        help="Git commit range to inspect for changed note files",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print issue IDs without updating Sentry",
    )
    args = parser.parse_args()

    token = os.environ.get("SENTRY_AUTH_TOKEN")
    if not token:
        raise RuntimeError("Missing SENTRY_AUTH_TOKEN env var.")

    repo_root = Path(args.repo_root).resolve()
    note_dir = Path(args.note_dir)
    commit_range = args.commit_range.strip() or default_commit_range()

    note_files = note_files_for_commit_range(repo_root, commit_range, note_dir)
    issue_ids = collect_issue_ids(note_files)

    if not note_files:
        print(f"No note files changed in {commit_range}.")
        return 0

    if not issue_ids:
        print(f"No Sentry issue IDs were found in {len(note_files)} note file(s).")
        return 0

    for issue_id in issue_ids:
        if args.dry_run:
            print(f"Would resolve Sentry issue {issue_id}")
            continue
        resolve_issue(args.base_url, args.org, token, issue_id)
        print(f"Resolved Sentry issue {issue_id}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
