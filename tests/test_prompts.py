"""Tests for system prompts."""

from claude_swarm.prompts import (
    PLANNER_SYSTEM_PROMPT,
    REVIEWER_SYSTEM_PROMPT,
    WORKER_SYSTEM_PROMPT,
)


def test_planner_prompt_format():
    result = PLANNER_SYSTEM_PROMPT.format(max_workers=4)
    assert "4" in result
    assert "{max_workers}" not in result
    # Literal braces should survive .format()
    assert '"original_task"' in result


def test_planner_prompt_preserves_json_braces():
    result = PLANNER_SYSTEM_PROMPT.format(max_workers=8)
    assert "{" in result  # JSON example braces should be preserved


def test_worker_prompt_format():
    result = WORKER_SYSTEM_PROMPT.format(
        task_description="Do something",
        target_files="- file.py",
        acceptance_criteria="- It works",
    )
    assert "Do something" in result
    assert "- file.py" in result
    assert "- It works" in result


def test_worker_prompt_no_extra_placeholders():
    result = WORKER_SYSTEM_PROMPT.format(
        task_description="x",
        target_files="y",
        acceptance_criteria="z",
    )
    # No remaining {placeholders}
    import re
    remaining = re.findall(r"\{[a-z_]+\}", result)
    assert remaining == []


def test_reviewer_prompt_no_format_placeholders():
    # REVIEWER_SYSTEM_PROMPT should have no .format() placeholders
    import re
    placeholders = re.findall(r"\{[a-z_]+\}", REVIEWER_SYSTEM_PROMPT)
    assert placeholders == []
