"""Utility helpers for claude-swarm."""

from __future__ import annotations

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

from claude_swarm.errors import SwarmError


async def run_agent(prompt: str, options: ClaudeAgentOptions) -> ResultMessage:
    """Consume the query() async stream and return the final ResultMessage.

    The Claude Agent SDK's query() returns an AsyncIterator[Message].
    Every invocation must consume the full stream to get the result.
    """
    result: ResultMessage | None = None
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, ResultMessage):
            result = message
    if result is None:
        raise SwarmError("Agent produced no ResultMessage")
    return result
