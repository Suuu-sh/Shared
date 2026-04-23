#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

DEFAULT_BASE_URL = "https://sentry.io"
DEFAULT_ORG = "suu-hb"
DEFAULT_PROJECT = "go"
DEFAULT_BASE_BRANCH = "main"
DEFAULT_TIME_RANGE = "24h"
DEFAULT_ENVIRONMENT = "production"
DEFAULT_LIMIT = 3
DEFAULT_BRANCH_PREFIX = "sentry/fix"
DEFAULT_NOTE_DIR = ".codex/sentry-fix"
LEGACY_BRANCH_PREFIX = "feature/sentry"
MAX_LIMIT = 50

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
BRANCH_SAFE_RE = re.compile(r"[^a-z0-9._-]+")
JST = dt.timezone(dt.timedelta(hours=9), name="JST")


def redact_string(value: str) -> str:
    value = EMAIL_RE.sub("[REDACTED_EMAIL]", value)
    value = IP_RE.sub("[REDACTED_IP]", value)
    return value


def redact_data(value: Any) -> Any:
    if isinstance(value, str):
        return redact_string(value)
    if isinstance(value, list):
        return [redact_data(item) for item in value]
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if key.lower() in {"email", "ip", "ip_address"}:
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = redact_data(item)
        return redacted
    return value


def pick(data: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return default


def slugify(value: str, fallback: str = "issue") -> str:
    value = redact_string(value).strip().lower()
    value = BRANCH_SAFE_RE.sub("-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return value or fallback


def parse_iso_datetime(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def format_datetime(value: str | None) -> str:
    parsed = parse_iso_datetime(value)
    if not parsed:
        return redact_string(value or "-")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(JST).strftime("%Y-%m-%d %H:%M:%S JST")


def format_scalar(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, (dict, list)):
        return json.dumps(redact_data(value), ensure_ascii=False, sort_keys=True)
    return redact_string(str(value))


def format_tags(tags: Any) -> list[str]:
    if not tags:
        return []
    lines: list[str] = []
    if isinstance(tags, dict):
        for key, value in sorted(tags.items()):
            lines.append(f"- `{redact_string(str(key))}`: `{format_scalar(value)}`")
        return lines
    if isinstance(tags, list):
        for item in tags:
            if isinstance(item, dict):
                key = item.get("key") or item.get("name") or item.get("0")
                value = item.get("value") or item.get("1")
                if key is not None:
                    lines.append(f"- `{redact_string(str(key))}`: `{format_scalar(value)}`")
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                lines.append(f"- `{redact_string(str(item[0]))}`: `{format_scalar(item[1])}`")
    return lines


def ensure_command(name: str) -> None:
    if shutil.which(name) is None:
        raise RuntimeError(f"Required command not found: {name}")


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


def configure_git_identity(repo_root: Path) -> None:
    name = run_command(
        ["git", "config", "--get", "user.name"],
        cwd=repo_root,
        capture_output=True,
        check=False,
    ).stdout.strip()
    if not name:
        run_command(
            ["git", "config", "user.name", "github-actions[bot]"], cwd=repo_root
        )

    email = run_command(
        ["git", "config", "--get", "user.email"],
        cwd=repo_root,
        capture_output=True,
        check=False,
    ).stdout.strip()
    if not email:
        run_command(
            ["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"],
            cwd=repo_root,
        )


def build_url(base_url: str, path: str, params: dict[str, Any] | None = None) -> str:
    from urllib.parse import urlencode

    url = f"{base_url.rstrip('/')}{path}"
    if params:
        url = f"{url}?{urlencode(params, doseq=True)}"
    return url


def next_cursor(link_header: str | None) -> str | None:
    if not link_header:
        return None
    for part in link_header.split(","):
        if 'rel="next"' in part and 'results="true"' in part:
            match = re.search(r'cursor="([^"]+)"', part)
            if match:
                return match.group(1)
    return None


def request_json(url: str, token: str, retries: int = 1) -> tuple[Any, Any]:
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen

    req = Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/json")

    attempt = 0
    while True:
        try:
            with urlopen(req) as resp:
                body = resp.read().decode("utf-8")
                data = json.loads(body) if body else None
                return data, resp.headers
        except HTTPError as err:
            body = err.read().decode("utf-8", "ignore")
            if attempt < retries and (err.code >= 500 or err.code == 429):
                attempt += 1
                continue
            raise RuntimeError(
                f"HTTP {err.code} for {url}: {body or 'request failed'}"
            ) from err
        except URLError as err:
            if attempt < retries:
                attempt += 1
                continue
            raise RuntimeError(f"Network error for {url}: {err.reason}") from err


def paged_get(
    base_url: str,
    path: str,
    params: dict[str, Any],
    token: str,
    limit: int,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    cursor = None
    while len(results) < limit:
        page_params = dict(params)
        page_params["per_page"] = min(MAX_LIMIT, limit - len(results))
        if cursor:
            page_params["cursor"] = cursor
        url = build_url(base_url, path, page_params)
        data, headers = request_json(url, token)
        if not data:
            break
        if isinstance(data, list):
            results.extend(data)
        else:
            raise RuntimeError(f"Unexpected response shape from {path}: {type(data)}")
        cursor = next_cursor(headers.get("Link"))
        if not cursor:
            break
    return results[:limit]


class SentryClient:
    def __init__(self, base_url: str, org: str, project: str, token: str) -> None:
        self.base_url = base_url
        self.org = org
        self.project = project
        self.token = token

    def list_issues(
        self,
        *,
        time_range: str,
        environment: str,
        query: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "statsPeriod": time_range,
            "environment": environment,
        }
        if query:
            params["query"] = query
        path = f"/api/0/projects/{self.org}/{self.project}/issues/"
        issues = paged_get(self.base_url, path, params, self.token, limit)
        issues.sort(
            key=lambda item: parse_iso_datetime(
                pick(item, "lastSeen", "last_seen", default="")
            )
            or dt.datetime.min.replace(tzinfo=dt.timezone.utc),
            reverse=True,
        )
        return issues

    def issue_detail(self, issue_id: str) -> dict[str, Any]:
        path = f"/api/0/issues/{issue_id}/"
        data, _ = request_json(build_url(self.base_url, path), self.token)
        if not isinstance(data, dict):
            raise RuntimeError(f"Unexpected issue detail shape for {issue_id}")
        return data

    def issue_events(self, issue_id: str, limit: int = 20) -> list[dict[str, Any]]:
        path = f"/api/0/issues/{issue_id}/events/"
        events = paged_get(self.base_url, path, {}, self.token, limit)
        events.sort(
            key=lambda item: parse_iso_datetime(
                pick(item, "dateCreated", "date_created", default="")
            )
            or dt.datetime.min.replace(tzinfo=dt.timezone.utc),
            reverse=True,
        )
        return events

    def event_detail(self, event_id: str) -> dict[str, Any]:
        path = f"/api/0/projects/{self.org}/{self.project}/events/{event_id}/"
        data, _ = request_json(build_url(self.base_url, path), self.token)
        if not isinstance(data, dict):
            raise RuntimeError(f"Unexpected event detail shape for {event_id}")
        data = dict(data)
        data.pop("entries", None)
        return data


def render_report(
    *,
    issue: dict[str, Any],
    latest_event: dict[str, Any],
    org: str,
    project: str,
    generated_at: dt.datetime,
    issue_url: str,
) -> str:
    short_id = format_scalar(pick(issue, "shortId", "short_id", default="unknown"))
    title = format_scalar(pick(issue, "title", default="(no title)"))
    culprit = format_scalar(
        pick(issue, "culprit", default=pick(latest_event, "culprit", default="-"))
    )
    count = format_scalar(pick(issue, "count", default="-"))
    first_seen = format_datetime(
        pick(issue, "firstSeen", "first_seen", default=None)
    )
    last_seen = format_datetime(pick(issue, "lastSeen", "last_seen", default=None))
    issue_level = format_scalar(pick(issue, "level", default="-"))
    issue_status = format_scalar(pick(issue, "status", default="unresolved"))

    event_id = format_scalar(
        pick(latest_event, "eventID", "eventId", "id", default="-")
    )
    event_time = format_datetime(
        pick(latest_event, "dateCreated", "date_created", default=None)
    )
    event_environment = format_scalar(pick(latest_event, "environment", default="-"))
    event_release = format_scalar(pick(latest_event, "release", default="-"))
    event_platform = format_scalar(pick(latest_event, "platform", default="-"))
    event_logger = format_scalar(pick(latest_event, "logger", default="-"))
    event_url = format_scalar(pick(latest_event, "url", default=issue_url))
    event_message = format_scalar(
        pick(latest_event, "message", "title", default=title)
    )
    tags = latest_event.get("tags") or issue.get("tags")

    generated_label = generated_at.astimezone(JST).strftime("%Y-%m-%d %H:%M:%S JST")
    tag_lines = format_tags(tags)
    if not tag_lines:
        tag_lines = ["- (なし)"]

    report_lines = [
        f"# Sentry fix prep: {short_id}",
        "",
        "## 概要",
        f"- Generated at: `{generated_label}`",
        f"- Org: `{org}`",
        f"- Project: `{project}`",
        f"- Status: `{issue_status}`",
        f"- Level: `{issue_level}`",
        f"- Count: `{count}`",
        f"- First seen: `{first_seen}`",
        f"- Last seen: `{last_seen}`",
        f"- Issue URL: {issue_url}",
        "",
        "## Issue",
        f"- Title: {title}",
        f"- Culprit: `{culprit}`",
        "",
        "## Latest event",
        f"- Event ID: `{event_id}`",
        f"- Timestamp: {event_time}",
        f"- Environment: `{event_environment}`",
        f"- Release: `{event_release}`",
        f"- Platform: `{event_platform}`",
        f"- Logger: `{event_logger}`",
        f"- Event URL: {event_url}",
        f"- Message: {event_message}",
        "",
        "## Tags",
        *tag_lines,
        "",
        "## Suggested next steps",
        "1. Latest event URL から再現経路を確認する。",
        "2. culprit に出ている backend の該当箇所を確認する。",
        "3. この branch に最小修正を入れる。",
        "4. `go test ./...` を通して PR を更新する。",
        "",
        "## Automation note",
        "この branch は backend Sentry fix prep workflow が自動生成したものです。",
        "自動化は issue の収集と branch/PR の作成までを担当し、実際の修正はこの branch 上で続けます。",
        "",
    ]
    return "\n".join(report_lines)


def candidate_branch_names(branch_prefix: str, short_id: str) -> list[str]:
    branch_id = slugify(short_id, "issue")
    branch_names = [f"{branch_prefix.rstrip('/')}/{branch_id}"]
    legacy_branch = f"{LEGACY_BRANCH_PREFIX.rstrip('/')}/{branch_id}"
    if legacy_branch not in branch_names:
        branch_names.append(legacy_branch)
    return branch_names


def branch_exists_remote(repo_root: Path, branch: str) -> bool:
    result = run_command(
        ["git", "ls-remote", "--heads", "origin", branch],
        cwd=repo_root,
        capture_output=True,
    )
    return bool(result.stdout.strip())


def checkout_branch(repo_root: Path, branch_names: list[str], base_branch: str) -> str:
    for branch in branch_names:
        if branch_exists_remote(repo_root, branch):
            run_command(["git", "fetch", "origin", branch], cwd=repo_root)
            run_command(["git", "checkout", "-B", branch, f"origin/{branch}"], cwd=repo_root)
            return branch

    branch = branch_names[0]
    run_command(["git", "checkout", "-B", branch, f"origin/{base_branch}"], cwd=repo_root)
    return branch


def working_tree_dirty(repo_root: Path) -> bool:
    result = run_command(
        ["git", "status", "--porcelain"],
        cwd=repo_root,
        capture_output=True,
    )
    return bool(result.stdout.strip())


def find_open_pr(repo_root: Path, branch_names: list[str]) -> dict[str, Any] | None:
    for branch in branch_names:
        result = run_command(
            [
                "gh",
                "pr",
                "list",
                "--head",
                branch,
                "--state",
                "open",
                "--limit",
                "1",
                "--json",
                "number,url,title,body",
            ],
            cwd=repo_root,
            capture_output=True,
        )
        data = json.loads(result.stdout or "[]")
        if not data:
            continue
        pr = data[0]
        pr["matched_branch"] = branch
        return pr
    return None


def resolve_branch(repo_root: Path, branch_names: list[str], base_branch: str) -> tuple[str, dict[str, Any] | None]:
    existing_pr = find_open_pr(repo_root, branch_names)
    if existing_pr:
        matched_branch = str(existing_pr["matched_branch"])
        return checkout_branch(repo_root, [matched_branch], base_branch), existing_pr

    return checkout_branch(repo_root, branch_names, base_branch), None


def ensure_pr(repo_root: Path, branch: str, base_branch: str, title: str, body_file: Path) -> str:
    existing = find_open_pr(repo_root, [branch])
    if existing:
        run_command(
            [
                "gh",
                "pr",
                "edit",
                str(existing["number"]),
                "--title",
                title,
                "--body-file",
                str(body_file),
            ],
            cwd=repo_root,
        )
        return str(existing["url"])

    result = run_command(
        [
            "gh",
            "pr",
            "create",
            "--draft",
            "--base",
            base_branch,
            "--head",
            branch,
            "--title",
            title,
            "--body-file",
            str(body_file),
        ],
        cwd=repo_root,
        capture_output=True,
    )
    return result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch unresolved Sentry issues and open/update draft PRs."
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
        "--project",
        default=os.environ.get("SENTRY_PROJECT", DEFAULT_PROJECT),
        help="Sentry project slug",
    )
    parser.add_argument(
        "--base-branch",
        default=os.environ.get("SENTRY_BASE_BRANCH", DEFAULT_BASE_BRANCH),
        help="Base branch for generated triage branches",
    )
    parser.add_argument(
        "--time-range",
        default=os.environ.get("SENTRY_TIME_RANGE", DEFAULT_TIME_RANGE),
        help="Sentry time range (e.g. 24h)",
    )
    parser.add_argument(
        "--environment",
        default=os.environ.get("SENTRY_ENVIRONMENT", DEFAULT_ENVIRONMENT),
        help="Sentry environment filter",
    )
    parser.add_argument(
        "--query",
        default=os.environ.get("SENTRY_QUERY", "is:unresolved"),
        help="Sentry issue search query",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=int(os.environ.get("SENTRY_LIMIT", str(DEFAULT_LIMIT))),
        help="Maximum number of issues to process",
    )
    parser.add_argument(
        "--branch-prefix",
        default=os.environ.get("SENTRY_BRANCH_PREFIX", DEFAULT_BRANCH_PREFIX),
        help="Git branch prefix for generated fix branches",
    )
    parser.add_argument(
        "--note-dir",
        default=os.environ.get("SENTRY_NOTE_DIR", DEFAULT_NOTE_DIR),
        help="Directory where generated Sentry notes are stored",
    )
    parser.add_argument(
        "--skip-pr",
        action="store_true",
        help="Only update branches, do not open or edit PRs",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and render notes without touching git or PRs",
    )
    args = parser.parse_args()

    token = os.environ.get("SENTRY_AUTH_TOKEN")
    if not token:
        raise RuntimeError("Missing SENTRY_AUTH_TOKEN env var.")

    ensure_command("git")
    if not args.skip_pr:
        ensure_command("gh")

    repo_root = Path(os.environ.get("SENTRY_REPO_ROOT") or os.environ.get("GITHUB_WORKSPACE") or os.getcwd()).resolve()
    if args.limit < 1:
        raise RuntimeError("--limit must be >= 1")

    client = SentryClient(args.base_url, args.org, args.project, token)
    issues = client.list_issues(
        time_range=args.time_range,
        environment=args.environment,
        query=args.query,
        limit=min(args.limit, MAX_LIMIT),
    )

    if not issues:
        print("No matching unresolved Sentry issues were found.")
        return 0

    generated_at = dt.datetime.now(dt.timezone.utc)
    note_dir = Path(args.note_dir)
    processed = 0
    pr_urls: list[str] = []

    if args.dry_run:
        for issue in issues:
            issue_id = str(pick(issue, "id", default=""))
            short_id = str(pick(issue, "shortId", "short_id", default=issue_id or "issue"))
            latest_event = {}
            issue_detail = client.issue_detail(issue_id) if issue_id else {}
            merged_issue = {**issue_detail, **issue}
            events = client.issue_events(issue_id, limit=1) if issue_id else []
            if events:
                latest_event = client.event_detail(
                    str(pick(events[0], "eventID", "eventId", "id", default=""))
                )
            issue_url = format_scalar(pick(merged_issue, "permalink", default="-"))
            note = render_report(
                issue=merged_issue,
                latest_event=latest_event,
                org=args.org,
                project=args.project,
                generated_at=generated_at,
                issue_url=issue_url,
            )
            print(f"\n--- {short_id} ---\n{note}")
        return 0

    if working_tree_dirty(repo_root):
        raise RuntimeError(
            "Working tree is dirty. Please run the automation from a clean checkout."
        )

    configure_git_identity(repo_root)
    run_command(["git", "fetch", "origin", args.base_branch], cwd=repo_root)
    if not args.skip_pr:
        run_command(["gh", "auth", "status"], cwd=repo_root, capture_output=True)

    for issue in issues:
        issue_id = str(pick(issue, "id", default=""))
        short_id = str(pick(issue, "shortId", "short_id", default=issue_id or "issue"))
        branch_names = candidate_branch_names(args.branch_prefix, short_id)

        issue_detail = client.issue_detail(issue_id) if issue_id else {}
        merged_issue = {**issue_detail, **issue}
        latest_event: dict[str, Any] = {}
        if issue_id:
            events = client.issue_events(issue_id, limit=1)
            if events:
                latest_event_id = str(
                    pick(events[0], "eventID", "eventId", "id", default="")
                )
                if latest_event_id:
                    latest_event = client.event_detail(latest_event_id)

        issue_url = format_scalar(pick(merged_issue, "permalink", default="-"))
        note = render_report(
            issue=merged_issue,
            latest_event=latest_event,
            org=args.org,
            project=args.project,
            generated_at=generated_at,
            issue_url=issue_url,
        )

        branch, _ = resolve_branch(repo_root, branch_names, args.base_branch)
        print(f"Processing {short_id} -> {branch}")
        note_path = repo_root / note_dir / f"{slugify(short_id, 'issue')}.md"
        note_path.parent.mkdir(parents=True, exist_ok=True)
        note_changed = True
        if note_path.exists():
            note_changed = note_path.read_text(encoding="utf-8") != note
        if note_changed:
            note_path.write_text(note, encoding="utf-8")
            run_command(["git", "add", str(note_path.relative_to(repo_root))], cwd=repo_root)
            commit_message = f"fix(sentry): prepare {short_id}"
            run_command(["git", "commit", "-m", commit_message], cwd=repo_root)
            processed += 1
        else:
            print(f"{short_id}: note already up to date")

        run_command(["git", "push", "origin", branch], cwd=repo_root)

        if args.skip_pr:
            continue

        pr_title = f"fix(sentry): {short_id}"
        pr_url = ensure_pr(repo_root, branch, args.base_branch, pr_title, note_path)
        pr_urls.append(pr_url)
        print(f"PR: {pr_url}")

    print(f"Done. Updated {processed} branch(es).")
    if pr_urls:
        print("Open PRs:")
        for url in pr_urls:
            print(f"- {url}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
