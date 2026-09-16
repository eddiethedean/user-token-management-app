"""Architecture checks for the decomposed pipeline interaction routes."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parents[1]
PIPELINE_INTERACTION_MODULES = (
    "pipeline_csv.py",
    "pipeline_datasets.py",
    "pipeline_preview.py",
    "pipeline_runs.py",
    "pipeline_save.py",
)


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
            modules.update(
                f"{node.module}.{alias.name}"
                for alias in node.names
                if node.module == "app.ui.routes"
            )
    return modules


def test_pipeline_interaction_routes_do_not_import_the_monolith() -> None:
    routes_root = ROOT / "app" / "ui" / "routes"
    for module_name in PIPELINE_INTERACTION_MODULES:
        assert "app.ui.routes.pipeline" not in _imported_modules(routes_root / module_name)
