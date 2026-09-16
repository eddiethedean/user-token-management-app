"""Value-only pipeline authoring contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.application.dto import ActorContext, PipelineSummary


@dataclass(frozen=True)
class SavePipelineAuthoringCommand:
    name: str
    source_provider: str
    source_schema: str = ""
    source_table: str = ""
    destination_provider: str = ""
    destination_schema: str = ""
    destination_table: str = ""
    write_mode: str = ""
    destination_table_new: str = ""
    conflict_columns: str = ""
    source_upload_id: str = ""
    pipeline_id: str = ""


class PipelineAuthoringOperation(Protocol):
    def __call__(
        self, actor: ActorContext, command: SavePipelineAuthoringCommand
    ) -> PipelineSummary: ...
