"""Compatibility shims for Hedron 0.15 packaging gaps.

Hedron 0.15.0 lifespan still imports ``hedron.build.load_build_manifest``, but the
``hedron.build`` package was removed from the published wheel. Access Registry does
not use build manifests (``default_styles=False`` + ``/assets/theme.css``), so a
minimal stub is enough.

Removal criteria: drop this module and the ``import app.hedron_compat`` side-effect
in ``app.main`` when a Hedron release no longer imports
``hedron.build.load_build_manifest`` at app startup (check PyPI / upstream changelog
beyond 0.15.0).
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any


def _install_build_stub() -> None:
    if "hedron.build" in sys.modules:
        return

    mod = types.ModuleType("hedron.build")

    def load_build_manifest(build_dir: str | Path) -> dict[str, Any]:
        raise FileNotFoundError(f"No Hedron build manifest in {build_dir}")

    mod.load_build_manifest = load_build_manifest  # type: ignore[attr-defined]
    sys.modules["hedron.build"] = mod

    import hedron

    hedron.build = mod  # type: ignore[attr-defined]


_install_build_stub()
