"""Security guards for swarm agents — blocks dangerous tool invocations."""

from __future__ import annotations

import logging
import re
from typing import Any

from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny, ToolPermissionContext

logger = logging.getLogger(__name__)

_BASH_DENY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"git\s+push\s+.*--force\b"), "Force push is blocked"),
    (re.compile(r"git\s+push\s+.*-[a-zA-Z]*f\b"), "Force push is blocked"),
    (re.compile(r"git\s+checkout\s+(main|master)\b"), "Checking out protected branch is blocked"),
    (re.compile(r"git\s+switch\s+(main|master)\b"), "Switching to protected branch is blocked"),
    (re.compile(r"rm\s+-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*\s+/"), "Recursive delete on absolute path is blocked"),
    (re.compile(r"rm\s+-[a-zA-Z]*f[a-zA-Z]*r[a-zA-Z]*\s+/"), "Recursive delete on absolute path is blocked"),
    (re.compile(r"rm\s+.*-r\b.*-f\b.*\s+/"), "Recursive delete on absolute path is blocked"),
    (re.compile(r"rm\s+.*-f\b.*-r\b.*\s+/"), "Recursive delete on absolute path is blocked"),
    (re.compile(r"git\s+reset\s+--hard\b"), "Hard reset is blocked"),
    (re.compile(r"git\s+clean\s+-[a-zA-Z]*f"), "git clean -f is blocked"),
    (re.compile(r"DROP\s+TABLE", re.IGNORECASE), "DROP TABLE is blocked"),
    (re.compile(r"DELETE\s+FROM\s+\S+\s*;", re.IGNORECASE), "DELETE FROM without WHERE is blocked"),
    (re.compile(r"DELETE\s+FROM\s+\S+\s*$", re.IGNORECASE), "DELETE FROM without WHERE is blocked"),
    (re.compile(r"curl\s+.*\|\s*(?:ba|da|z)?sh\b"), "Piping curl to shell is blocked"),
    (re.compile(r"curl\s+.*\|\s*/\S*sh\b"), "Piping curl to shell is blocked"),
    (re.compile(r"wget\s+.*\|\s*(?:ba|da|z)?sh\b"), "Piping wget to shell is blocked"),
    (re.compile(r"wget\s+.*\|\s*/\S*sh\b"), "Piping wget to shell is blocked"),
]


def _check_bash_command(command: str) -> str | None:
    """Returns denial reason if blocked, None if allowed."""
    for pattern, reason in _BASH_DENY_PATTERNS:
        if pattern.search(command):
            return reason
    return None


async def swarm_can_use_tool(
    tool_name: str,
    tool_input: dict[str, Any],
    context: ToolPermissionContext,
) -> PermissionResultAllow | PermissionResultDeny:
    """Guard callback for swarm agents."""
    if tool_name != "Bash":
        return PermissionResultAllow()
    command = tool_input.get("command", "")
    reason = _check_bash_command(command)
    if reason is not None:
        logger.warning("Guard blocked command: %s (reason: %s)", command[:200] + ("..." if len(command) > 200 else ""), reason)
        return PermissionResultDeny(message=reason)
    return PermissionResultAllow()
