"""GitHub API wrapper using the gh CLI."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path

from claude_swarm.errors import GitHubError

logger = logging.getLogger(__name__)


async def _run_gh(args: list[str], cwd: Path) -> str:
    """Run a gh CLI command. Returns stdout on success, raises GitHubError on failure."""
    proc = await asyncio.create_subprocess_exec(
        "gh", *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        error = stderr.decode().strip() or stdout.decode().strip()
        raise GitHubError(f"gh {' '.join(args[:3])}... failed: {error}")
    return stdout.decode().strip()


def parse_repo_url(url: str) -> tuple[str, str]:
    """Parse owner/repo from a git remote URL.

    Supports:
      - git@github.com:owner/repo.git
      - https://github.com/owner/repo.git
      - https://github.com/owner/repo
      - ssh://git@github.com/owner/repo.git
    """
    # SSH: git@github.com:owner/repo.git
    m = re.match(r"git@github\.com:([^/]+)/([^/]+?)(?:\.git)?$", url)
    if m:
        return m.group(1), m.group(2)

    # HTTPS or ssh:// protocol
    m = re.match(r"(?:https?|ssh)://[^/]+/([^/]+)/([^/]+?)(?:\.git)?$", url)
    if m:
        return m.group(1), m.group(2)

    raise GitHubError(f"Cannot parse GitHub owner/repo from URL: {url}")


async def get_repo_slug(repo_path: Path) -> tuple[str, str]:
    """Detect owner/repo from git remote origin."""
    proc = await asyncio.create_subprocess_exec(
        "git", "remote", "get-url", "origin",
        cwd=str(repo_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise GitHubError("No git remote 'origin' found. Use --repo owner/repo or run inside a git repo with a remote.")
    url = stdout.decode().strip()
    return parse_repo_url(url)


async def list_issues(
    owner: str,
    repo_name: str,
    label: str,
    *,
    exclude_labels: list[str] | None = None,
    cwd: Path,
) -> list[dict]:
    """List open issues with a given label, excluding issues with any exclude_labels."""
    args = [
        "issue", "list",
        "--repo", f"{owner}/{repo_name}",
        "--label", label,
        "--json", "number,title,body,labels",
        "--state", "open",
        "--limit", "50",
    ]
    output = await _run_gh(args, cwd)
    if not output:
        return []
    issues = json.loads(output)

    if exclude_labels:
        exclude_set = set(exclude_labels)
        issues = [
            issue for issue in issues
            if not exclude_set.intersection(
                lbl["name"] if isinstance(lbl, dict) else lbl
                for lbl in issue.get("labels", [])
            )
        ]

    return issues


async def get_issue(
    owner: str,
    repo_name: str,
    issue_number: int,
    *,
    cwd: Path,
) -> dict:
    """Fetch a single issue by number."""
    output = await _run_gh(
        ["issue", "view", str(issue_number),
         "--repo", f"{owner}/{repo_name}",
         "--json", "number,title,body,labels"],
        cwd,
    )
    return json.loads(output)


async def add_label(
    owner: str, repo_name: str, issue_number: int, label: str, *, cwd: Path,
) -> None:
    """Add a label to an issue."""
    await _run_gh(
        ["issue", "edit", str(issue_number),
         "--repo", f"{owner}/{repo_name}",
         "--add-label", label],
        cwd,
    )


async def remove_label(
    owner: str, repo_name: str, issue_number: int, label: str, *, cwd: Path,
) -> None:
    """Remove a label from an issue."""
    await _run_gh(
        ["issue", "edit", str(issue_number),
         "--repo", f"{owner}/{repo_name}",
         "--remove-label", label],
        cwd,
    )


async def post_comment(
    owner: str, repo_name: str, issue_number: int, body: str, *, cwd: Path,
) -> None:
    """Post a comment on an issue."""
    await _run_gh(
        ["issue", "comment", str(issue_number),
         "--repo", f"{owner}/{repo_name}",
         "--body", body],
        cwd,
    )


async def close_issue(
    owner: str, repo_name: str, issue_number: int, *, cwd: Path,
) -> None:
    """Close an issue."""
    await _run_gh(
        ["issue", "close", str(issue_number),
         "--repo", f"{owner}/{repo_name}"],
        cwd,
    )


async def ensure_labels_exist(owner: str, repo_name: str, *, cwd: Path) -> None:
    """Create swarm, swarm:active, swarm:done, swarm:failed labels if missing."""
    labels = [
        ("swarm", "0e8a16", "Trigger swarm processing"),
        ("swarm:active", "1d76db", "Swarm is processing this issue"),
        ("swarm:done", "0e8a16", "Swarm completed successfully"),
        ("swarm:failed", "d93f0b", "Swarm processing failed"),
    ]
    for name, color, description in labels:
        try:
            await _run_gh(
                ["label", "create", name,
                 "--repo", f"{owner}/{repo_name}",
                 "--color", color,
                 "--description", description,
                 "--force"],
                cwd,
            )
        except GitHubError:
            # Label creation can fail if gh doesn't support --force; ignore
            logger.debug("Label %s may already exist", name)
