"""Tests for the framework-neutral pipeline command boundary."""

from __future__ import annotations

from typing import cast
from unittest.mock import Mock

from app.application import pipelines as application_pipelines
from app.connectors.locators import DefinitionSnapshot
from app.models import PipelineDefinition, User


def test_pipeline_commands_forward_explicit_policies(monkeypatch) -> None:
    save = Mock(return_value="saved")
    enqueue = Mock(return_value="queued")
    monkeypatch.setattr(application_pipelines, "save_pipeline", save)
    monkeypatch.setattr(application_pipelines, "enqueue_run", enqueue)
    route_policy = Mock(return_value=True)
    writer_policy = Mock(return_value=True)
    commands = application_pipelines.PipelineCommands(
        application_pipelines.PipelineDependencies(
            route_policy=route_policy,
            writer_policy=writer_policy,
        )
    )
    db = Mock()

    save_command = application_pipelines.SavePipelineCommand(
        user=cast(User, object()),
        name="route",
        source_provider="postgres",
        destination_provider="postgres",
        write_mode="append",
        available_providers={"postgres"},
    )
    enqueue_command = application_pipelines.EnqueuePipelineCommand(
        user=cast(User, object()),
        pipeline=cast(PipelineDefinition, object()),
        snapshot=cast(DefinitionSnapshot, object()),
    )

    assert commands.save(db, save_command) == "saved"
    assert commands.enqueue(db, enqueue_command) == "queued"
    assert save.call_args.kwargs["route_policy"] is route_policy
    assert save.call_args.kwargs["writer_policy"] is writer_policy
    assert enqueue.call_args.kwargs["route_policy"] is route_policy
    assert enqueue.call_args.kwargs["writer_policy"] is writer_policy
