"""Validate local Markdown target paths without requiring third-party tooling."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")


def _destination(raw: str) -> str:
    """Return a Markdown link destination, handling optional titles and angle brackets."""

    value = raw.strip()
    if value.startswith("<") and ">" in value:
        return value[1 : value.index(">")]
    return value.split(maxsplit=1)[0]


def _markdown_files() -> list[Path]:
    return sorted(
        path
        for path in ROOT.rglob("*.md")
        if ".git" not in path.parts and ".venv" not in path.parts
    )


def find_broken_links() -> tuple[list[str], int, int]:
    """Return broken-link messages, Markdown-file count, and local-link count."""

    broken: list[str] = []
    markdown_files = _markdown_files()
    local_links = 0

    for source in markdown_files:
        for match in MARKDOWN_LINK.finditer(source.read_text(encoding="utf-8")):
            destination = _destination(match.group(1))
            parsed = urlsplit(destination)
            if parsed.scheme or parsed.netloc or destination.startswith("#"):
                continue

            local_links += 1
            target = (source.parent / unquote(parsed.path)).resolve()
            try:
                target.relative_to(ROOT)
            except ValueError:
                broken.append(f"{source.relative_to(ROOT)}: link escapes repository: {destination}")
                continue

            if not target.exists():
                broken.append(f"{source.relative_to(ROOT)}: missing target: {destination}")

    return broken, len(markdown_files), local_links


def main() -> int:
    broken, markdown_count, local_link_count = find_broken_links()
    if broken:
        print(f"docs-check: {len(broken)} broken local link(s)")
        for message in broken:
            print(f"  - {message}")
        return 1

    print(
        "docs-check: "
        f"{markdown_count} Markdown files and {local_link_count} local links checked; all targets exist"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
