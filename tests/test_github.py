"""Tests for GitHub API wrapper."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from claude_swarm.errors import GitHubError
from claude_swarm.github import (
    _run_gh,
    add_label,
    close_issue,
    ensure_labels_exist,
    get_issue,
    get_repo_slug,
    list_issues,
    parse_repo_url,
    post_comment,
    remove_label,
)


class TestParseRepoUrl:
    def test_ssh_url(self):
        owner, repo = parse_repo_url("git@github.com:octocat/hello-world.git")
        assert owner == "octocat"
        assert repo == "hello-world"

    def test_https_url(self):
        owner, repo = parse_repo_url("https://github.com/octocat/hello-world")
        assert owner == "octocat"
        assert repo == "hello-world"

    def test_https_with_git_suffix(self):
        owner, repo = parse_repo_url("https://github.com/octocat/hello-world.git")
        assert owner == "octocat"
        assert repo == "hello-world"

    def test_ssh_protocol_url(self):
        owner, repo = parse_repo_url("ssh://git@github.com/octocat/hello-world.git")
        assert owner == "octocat"
        assert repo == "hello-world"

    def test_invalid_url_raises(self):
        with pytest.raises(GitHubError, match="Cannot parse"):
            parse_repo_url("not-a-url")


class TestGetRepoSlug:
    @pytest.mark.asyncio
    async def test_detects_from_remote(self, tmp_path):
        """Mock git remote to return a URL, verify parsing."""
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(
            return_value=(b"git@github.com:owner/repo.git\n", b""),
        )

        with patch("claude_swarm.github.asyncio.create_subprocess_exec", return_value=mock_proc):
            owner, repo = await get_repo_slug(tmp_path)
            assert owner == "owner"
            assert repo == "repo"

    @pytest.mark.asyncio
    async def test_no_remote_raises(self, tmp_path):
        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b"", b"fatal: no such remote"))

        with patch("claude_swarm.github.asyncio.create_subprocess_exec", return_value=mock_proc):
            with pytest.raises(GitHubError, match="No git remote"):
                await get_repo_slug(tmp_path)


class TestListIssues:
    @pytest.mark.asyncio
    async def test_returns_parsed_json(self, tmp_path):
        issues = [
            {"number": 1, "title": "Test", "body": "body", "labels": [{"name": "swarm"}]},
            {"number": 2, "title": "Test2", "body": "body2", "labels": [{"name": "swarm"}]},
        ]
        with patch("claude_swarm.github._run_gh", AsyncMock(return_value=json.dumps(issues))):
            result = await list_issues("owner", "repo", "swarm", cwd=tmp_path)
            assert len(result) == 2
            assert result[0]["number"] == 1

    @pytest.mark.asyncio
    async def test_empty_list(self, tmp_path):
        with patch("claude_swarm.github._run_gh", AsyncMock(return_value="")):
            result = await list_issues("owner", "repo", "swarm", cwd=tmp_path)
            assert result == []

    @pytest.mark.asyncio
    async def test_gh_failure_raises(self, tmp_path):
        with patch("claude_swarm.github._run_gh", AsyncMock(side_effect=GitHubError("fail"))):
            with pytest.raises(GitHubError):
                await list_issues("owner", "repo", "swarm", cwd=tmp_path)

    @pytest.mark.asyncio
    async def test_excludes_labels(self, tmp_path):
        issues = [
            {"number": 1, "title": "A", "body": "", "labels": [{"name": "swarm"}]},
            {"number": 2, "title": "B", "body": "", "labels": [{"name": "swarm"}, {"name": "swarm:active"}]},
        ]
        with patch("claude_swarm.github._run_gh", AsyncMock(return_value=json.dumps(issues))):
            result = await list_issues(
                "owner", "repo", "swarm",
                exclude_labels=["swarm:active"],
                cwd=tmp_path,
            )
            assert len(result) == 1
            assert result[0]["number"] == 1


class TestLabelOps:
    @pytest.mark.asyncio
    async def test_add_label(self, tmp_path):
        with patch("claude_swarm.github._run_gh", AsyncMock(return_value="")) as mock:
            await add_label("owner", "repo", 1, "swarm:active", cwd=tmp_path)
            mock.assert_called_once()
            args = mock.call_args[0][0]
            assert "issue" in args
            assert "edit" in args
            assert "--add-label" in args
            assert "swarm:active" in args

    @pytest.mark.asyncio
    async def test_remove_label(self, tmp_path):
        with patch("claude_swarm.github._run_gh", AsyncMock(return_value="")) as mock:
            await remove_label("owner", "repo", 1, "swarm", cwd=tmp_path)
            mock.assert_called_once()
            args = mock.call_args[0][0]
            assert "--remove-label" in args
            assert "swarm" in args


class TestPostComment:
    @pytest.mark.asyncio
    async def test_posts_comment(self, tmp_path):
        with patch("claude_swarm.github._run_gh", AsyncMock(return_value="")) as mock:
            await post_comment("owner", "repo", 1, "hello", cwd=tmp_path)
            mock.assert_called_once()
            args = mock.call_args[0][0]
            assert "issue" in args
            assert "comment" in args
            assert "hello" in args


class TestCloseIssue:
    @pytest.mark.asyncio
    async def test_closes_issue(self, tmp_path):
        with patch("claude_swarm.github._run_gh", AsyncMock(return_value="")) as mock:
            await close_issue("owner", "repo", 1, cwd=tmp_path)
            mock.assert_called_once()
            args = mock.call_args[0][0]
            assert "issue" in args
            assert "close" in args


class TestGetIssue:
    @pytest.mark.asyncio
    async def test_returns_parsed_issue(self, tmp_path):
        issue = {"number": 42, "title": "T", "body": "B", "labels": []}
        with patch("claude_swarm.github._run_gh", AsyncMock(return_value=json.dumps(issue))):
            result = await get_issue("owner", "repo", 42, cwd=tmp_path)
            assert result["number"] == 42


class TestEnsureLabels:
    @pytest.mark.asyncio
    async def test_creates_labels(self, tmp_path):
        with patch("claude_swarm.github._run_gh", AsyncMock(return_value="")) as mock:
            await ensure_labels_exist("owner", "repo", cwd=tmp_path)
            # Should create 4 labels
            assert mock.call_count == 4

    @pytest.mark.asyncio
    async def test_partial_failure_continues(self, tmp_path):
        call_count = 0

        async def side_effect(args, cwd):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise GitHubError("label create failed")
            return ""

        with patch("claude_swarm.github._run_gh", AsyncMock(side_effect=side_effect)):
            await ensure_labels_exist("owner", "repo", cwd=tmp_path)
        assert call_count == 4  # all 4 attempted despite failure on 2nd
