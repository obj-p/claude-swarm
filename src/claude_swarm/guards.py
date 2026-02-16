"""Security guards for swarm agents — blocks dangerous tool invocations."""

from __future__ import annotations

import logging
import re
from typing import Any

from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny, ToolPermissionContext

logger = logging.getLogger(__name__)

_BASH_DENY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"git\s+push\s+.*--force\b"), "Force push is blocked"),
    (re.compile(r"git\s+push\s+.*-[a-zA-Z]*f[a-zA-Z]*\b"), "Force push is blocked"),
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
    # 1. Privilege escalation — sudo (command-position anchored)
    (re.compile(r"(?:^|[;&|]\s*|&&\s*|\|\|\s*|\|\s*)sudo\b"), "sudo is blocked"),
    # 2. Filesystem destruction — mkfs, dd to devices, shred (command-position anchored)
    (re.compile(r"(?:^|[;&|]\s*|&&\s*|\|\|\s*|\|\s*)mkfs\b"), "mkfs is blocked"),
    (re.compile(r"\bdd\b.*\bof\s*=\s*/dev/"), "dd writing to device is blocked"),
    (re.compile(r"(?:^|[;&|]\s*|&&\s*|\|\|\s*|\|\s*)shred\b"), "shred is blocked"),
    # 3. Exfiltration via netcat — pipe to nc/netcat/ncat
    (re.compile(r"\|\s*nc\b"), "Piping to nc (netcat) is blocked"),
    (re.compile(r"\|\s*netcat\b"), "Piping to netcat is blocked"),
    (re.compile(r"\|\s*ncat\b"), "Piping to ncat is blocked"),
    # 4. Reverse shells — /dev/tcp, /dev/udp, nc -e
    (re.compile(r"/dev/tcp/"), "/dev/tcp access is blocked (reverse shell vector)"),
    (re.compile(r"/dev/udp/"), "/dev/udp access is blocked (reverse shell vector)"),
    (re.compile(r"\bnc\b[^;&|\n]*-[a-zA-Z]*e\b"), "nc -e is blocked (reverse shell vector)"),
    (re.compile(r"\bncat\b[^;&|\n]*-[a-zA-Z]*e\b"), "ncat -e is blocked (reverse shell vector)"),
    # 5. System path overwrite — redirect/tee to /etc, /var, /usr, /sys, /proc
    (re.compile(r">\s*/etc/"), "Overwriting /etc/ is blocked"),
    (re.compile(r">\s*/var/"), "Overwriting /var/ is blocked"),
    (re.compile(r">\s*/usr/"), "Overwriting /usr/ is blocked"),
    (re.compile(r">\s*/sys/"), "Overwriting /sys/ is blocked"),
    (re.compile(r">\s*/proc/"), "Overwriting /proc/ is blocked"),
    (re.compile(r"\btee\s+/etc/"), "tee to /etc/ is blocked"),
    (re.compile(r"\btee\s+/var/"), "tee to /var/ is blocked"),
    (re.compile(r"\btee\s+/usr/"), "tee to /usr/ is blocked"),
    (re.compile(r"\btee\s+/sys/"), "tee to /sys/ is blocked"),
    (re.compile(r"\btee\s+/proc/"), "tee to /proc/ is blocked"),
    # 6. Process persistence — nohup, crontab, at (command-position anchored)
    (re.compile(r"(?:^|[;&|]\s*|&&\s*|\|\|\s*|\|\s*)nohup\b"), "nohup is blocked"),
    (re.compile(r"(?:^|[;&|]\s*|&&\s*|\|\|\s*|\|\s*)crontab\b"), "crontab is blocked"),
    (re.compile(r"(?:^|[;&|]\s*|&&\s*|\|\|\s*)at\s"), "at scheduler is blocked"),
    # 7. Indirect destructive ops — find with -delete or -exec rm on absolute paths
    (re.compile(r"\bfind\s+/\S*\s.*-delete\b"), "find -delete on absolute path is blocked"),
    (re.compile(r"\bfind\s+/\S*\s.*-exec\s+rm\b"), "find -exec rm on absolute path is blocked"),
    # 8. Dangerous chmod — 777 or system paths
    (re.compile(r"\bchmod\b.*\b777\b"), "chmod 777 is blocked"),
    (re.compile(r"\bchmod\b.*\s+/etc/"), "chmod on /etc/ is blocked"),
    (re.compile(r"\bchmod\b.*\s+/usr/"), "chmod on /usr/ is blocked"),
    (re.compile(r"\bchmod\b.*\s+/sys/"), "chmod on /sys/ is blocked"),
    # 9. Fork bombs
    (re.compile(r":\(\)\s*\{"), "Fork bomb pattern is blocked"),
    # 10. Git remote abuse
    (re.compile(r"git\s+remote\s+add\b"), "Adding git remotes is blocked"),
    (re.compile(r"git\s+remote\s+set-url\b"), "Changing git remote URLs is blocked"),
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
