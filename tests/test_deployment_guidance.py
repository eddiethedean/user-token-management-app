from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_DOC = PROJECT_ROOT / "docs" / "deploy.md"


def _section(source: str, heading: str, next_heading: str) -> str:
    start = source.index(heading)
    end = source.index(next_heading, start + len(heading))
    return source[start:end]


def test_postgres_workbench_instructions_create_required_password_blocklist() -> None:
    deploy = DEPLOY_DOC.read_text(encoding="utf-8")
    workbench = _section(
        deploy,
        "## Operational Workbench deployment",
        "## Production deployment",
    )

    required_commands = (
        "mkdir -p deployment",
        "cp /path/to/approved/password-blocklist.txt deployment/password-blocklist.txt",
        "chmod 600 deployment/password-blocklist.txt",
    )
    missing = [command for command in required_commands if command not in workbench]

    assert not missing, (
        "PostgreSQL/live Workbench setup must create the configured password blocklist; "
        f"missing commands: {missing}"
    )


def test_connect_instructions_retain_spool_directory_in_file_only_bundle() -> None:
    deploy = DEPLOY_DOC.read_text(encoding="utf-8")
    production = deploy[deploy.index("## Production deployment") :]
    committed_spool_files = (
        list((PROJECT_ROOT / "deployment" / "spool").rglob("*"))
        if (PROJECT_ROOT / "deployment" / "spool").is_dir()
        else []
    )
    retained_by_repository = any(path.is_file() for path in committed_spool_files)
    retained_by_instructions = re.search(
        r"(?m)^(?:touch|install|printf\b.*>)\s+[^\n]*deployment/spool/[^/\s]+\s*$",
        production,
    )

    assert retained_by_repository or retained_by_instructions, (
        "Connect setup must put at least one file in deployment/spool so rsconnect's "
        "file-only bundle retains the directory"
    )


def test_readme_operational_commands_use_one_in_process_app() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    introduction = "Its commands are:"
    start = readme.index(introduction) + len(introduction)
    command_block = readme[start : readme.index("```", readme.index("```", start) + 3)]

    assert "scripts/run-workbench.sh web" in command_block
    assert "scripts/run-workbench.sh worker" not in command_block
    assert "scripts/run-workbench.sh janitor" not in command_block


def test_documented_database_provisioning_helpers_have_a_consumer() -> None:
    env_example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    helper_names = set(re.findall(r"(?m)^# (DB_[A-Z0-9_]+)=", env_example))
    if not helper_names:
        return

    candidate_paths = [PROJECT_ROOT / "README.md", PROJECT_ROOT / "Makefile"]
    candidate_paths.extend((PROJECT_ROOT / "scripts").rglob("*"))
    candidate_paths.extend((PROJECT_ROOT / "docs").rglob("*.md"))
    consumers: dict[str, list[Path]] = {name: [] for name in helper_names}
    for path in candidate_paths:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for name in helper_names:
            if name in text:
                consumers[name].append(path.relative_to(PROJECT_ROOT))

    unused = sorted(name for name, paths in consumers.items() if not paths)
    assert not unused, (
        ".env.example says its PostgreSQL DB_* values are provisioning inputs, but these "
        f"variables have no documented or scripted consumer: {unused}"
    )
