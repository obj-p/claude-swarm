"""Tests for system prompts."""

from claude_swarm.prompts import (
    CONFLICT_RESOLVER_SYSTEM_PROMPT,
    PLANNER_SYSTEM_PROMPT,
    REVIEWER_SYSTEM_PROMPT,
    WORKER_COORDINATION_INSTRUCTIONS,
    WORKER_COORDINATION_SECTION,
    WORKER_COUPLING_SECTION,
    WORKER_NOTES_SECTION,
    WORKER_RETRY_CONTEXT,
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


def test_worker_retry_context_format():
    result = WORKER_RETRY_CONTEXT.format(error_context="some error happened")
    assert "some error happened" in result
    assert "Previous Attempt Failed" in result


def test_conflict_resolver_prompt_non_empty():
    assert isinstance(CONFLICT_RESOLVER_SYSTEM_PROMPT, str)
    assert len(CONFLICT_RESOLVER_SYSTEM_PROMPT) > 0
    assert "merge conflict" in CONFLICT_RESOLVER_SYSTEM_PROMPT.lower()


def test_worker_notes_section_format():
    result = WORKER_NOTES_SECTION.format(
        notes_dir_path="/tmp/notes",
        worker_id="w1",
    )
    assert "/tmp/notes" in result
    assert "w1" in result
    assert "Write tool" in result or "Write" in result


def test_notes_section_preserves_json_braces():
    result = WORKER_NOTES_SECTION.format(
        notes_dir_path="/tmp/notes",
        worker_id="w1",
    )
    # JSON example braces should survive .format() as single braces
    assert '"worker_id"' in result
    assert "{\n" in result  # Opening brace of JSON example
    assert "{{" not in result  # No double-braces in output


def test_planner_includes_coordination_guidance():
    result = PLANNER_SYSTEM_PROMPT.format(max_workers=4)
    assert "coordination_notes" in result
    assert "Coordination" in result
    assert "coupled_with" in result
    assert "shared_interfaces" in result


def test_planner_includes_shared_notes_in_coordination():
    result = PLANNER_SYSTEM_PROMPT.format(max_workers=4)
    assert "Shared Notes" in result
    assert "Directed Messages" in result


def test_worker_coordination_instructions_format():
    result = WORKER_COORDINATION_INSTRUCTIONS.format(
        coordination_instructions="Write a note about the API schema",
    )
    assert "Write a note about the API schema" in result
    assert "Coordination Instructions" in result


def test_worker_coordination_section_format():
    result = WORKER_COORDINATION_SECTION.format(
        coordination_dir_path="/tmp/coordination",
        worker_id="w1",
    )
    assert "/tmp/coordination" in result
    assert "w1" in result
    assert "Shared Notes" in result
    assert "Directed Messages" in result
    assert "Status Updates" in result
    assert "message_type" in result


def test_coordination_section_preserves_json_braces():
    result = WORKER_COORDINATION_SECTION.format(
        coordination_dir_path="/tmp/coordination",
        worker_id="w1",
    )
    assert '"worker_id"' in result
    assert "{{" not in result


def test_worker_coupling_section_format():
    result = WORKER_COUPLING_SECTION.format(
        coupled_workers="w2, w3",
        shared_interfaces="User API schema, event payload",
    )
    assert "w2, w3" in result
    assert "User API schema" in result
    assert "Coupled Workers" in result


def test_coupling_section_no_extra_placeholders():
    import re
    result = WORKER_COUPLING_SECTION.format(
        coupled_workers="w2",
        shared_interfaces="API schema",
    )
    remaining = re.findall(r"\{[a-z_]+\}", result)
    assert remaining == []
