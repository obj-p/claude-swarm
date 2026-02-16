"""Tests for Pydantic models."""

import json

import pytest
from pydantic import ValidationError

from claude_swarm.models import IssueConfig, SwarmResult, TaskPlan, WorkerResult, WorkerTask


class TestWorkerTask:
    def test_minimal(self):
        t = WorkerTask(worker_id="w1", title="Do thing", description="Details")
        assert t.worker_id == "w1"
        assert t.target_files == []
        assert t.acceptance_criteria == []

    def test_coordination_notes_default(self):
        t = WorkerTask(worker_id="w1", title="Do thing", description="Details")
        assert t.coordination_notes == ""

    def test_full(self):
        t = WorkerTask(
            worker_id="w1",
            title="Title",
            description="Desc",
            target_files=["a.py"],
            acceptance_criteria=["works"],
        )
        assert t.target_files == ["a.py"]
        assert t.acceptance_criteria == ["works"]


class TestTaskPlan:
    def test_minimal(self):
        p = TaskPlan(
            original_task="task",
            reasoning="why",
            tasks=[WorkerTask(worker_id="w1", title="t", description="d")],
        )
        assert p.integration_notes == ""
        assert p.test_command is None
        assert p.build_command is None

    def test_json_schema_valid(self):
        schema = TaskPlan.model_json_schema()
        assert "properties" in schema
        assert "tasks" in schema["properties"]

    def test_roundtrip(self):
        p = TaskPlan(
            original_task="task",
            reasoning="why",
            tasks=[WorkerTask(worker_id="w1", title="t", description="d")],
            test_command="pytest",
        )
        json_str = p.model_dump_json()
        p2 = TaskPlan.model_validate_json(json_str)
        assert p2.original_task == p.original_task
        assert len(p2.tasks) == 1
        assert p2.test_command == "pytest"

    def test_validate_from_dict(self, sample_task_plan_dict):
        p = TaskPlan.model_validate(sample_task_plan_dict)
        assert p.original_task == "Add logging"
        assert len(p.tasks) == 1
        assert p.tasks[0].worker_id == "worker-1"

    def test_missing_required_field_raises(self):
        with pytest.raises(ValidationError):
            TaskPlan(original_task="task", reasoning="why", tasks=None)


class TestWorkerResult:
    def test_defaults(self):
        r = WorkerResult(worker_id="w1", success=True)
        assert r.cost_usd is None
        assert r.duration_ms is None
        assert r.summary is None
        assert r.files_changed == []
        assert r.error is None

    def test_attempt_default(self):
        r = WorkerResult(worker_id="w1", success=True)
        assert r.attempt == 1

    def test_model_used_default(self):
        r = WorkerResult(worker_id="w1", success=True)
        assert r.model_used is None


class TestIssueConfig:
    def test_task_description(self):
        ic = IssueConfig(
            issue_number=1, owner="o", repo_name="r",
            title="[swarm] Fix the tests",
            body="The tests are broken.",
        )
        assert ic.task_description == "Fix the tests\n\nThe tests are broken."

    def test_task_description_no_prefix(self):
        ic = IssueConfig(
            issue_number=1, owner="o", repo_name="r",
            title="Add logging",
            body="Details here.",
        )
        assert ic.task_description == "Add logging\n\nDetails here."

    def test_task_description_empty_body(self):
        ic = IssueConfig(
            issue_number=1, owner="o", repo_name="r",
            title="Add logging",
            body="",
        )
        assert ic.task_description == "Add logging"
        assert not ic.task_description.endswith("\n")

    def test_invalid_oversight_coerced_to_none(self):
        ic = IssueConfig(
            issue_number=1, owner="o", repo_name="r",
            title="T", body="B",
            oversight="yolo",
        )
        assert ic.oversight is None


class TestSwarmResult:
    def test_defaults(self):
        plan = TaskPlan(
            original_task="t",
            reasoning="r",
            tasks=[WorkerTask(worker_id="w1", title="t", description="d")],
        )
        r = SwarmResult(run_id="123", task="t", plan=plan)
        assert r.worker_results == []
        assert r.integration_success is False
        assert r.pr_url is None
        assert r.total_cost_usd == 0.0
        assert r.duration_ms == 0
