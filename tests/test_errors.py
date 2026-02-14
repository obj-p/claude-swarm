"""Tests for error hierarchy."""

from claude_swarm.errors import (
    IntegrationError,
    MergeConflictError,
    PlanningError,
    SwarmError,
    WorkerError,
    WorktreeError,
)


def test_all_errors_inherit_from_swarm_error():
    for cls in (WorktreeError, WorkerError, IntegrationError, PlanningError, MergeConflictError):
        assert issubclass(cls, SwarmError)


def test_merge_conflict_inherits_from_integration_error():
    assert issubclass(MergeConflictError, IntegrationError)


def test_merge_conflict_extra_fields():
    err = MergeConflictError("conflict", conflicting_branches=["a", "b"], diff_context="@@diff@@")
    assert err.conflicting_branches == ["a", "b"]
    assert err.diff_context == "@@diff@@"
    assert str(err) == "conflict"


def test_merge_conflict_defaults():
    err = MergeConflictError("oops")
    assert err.conflicting_branches == []
    assert err.diff_context is None
