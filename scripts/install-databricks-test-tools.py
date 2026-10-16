"""Fetch checksum-pinned local test servers; pytest itself never downloads tools."""

from __future__ import annotations

import hashlib
import platform
import tempfile
from pathlib import Path

import httpx2

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / ".cache" / "databricks-tools"
WIREMOCK_VERSION = "3.13.2"
TOXIPROXY_VERSION = "2.12.0"
TOXIPROXY_HASHES = {
    ("Darwin", "arm64"): "aa299966b52f16a8594f1cd0d1e9049dc2e8fe2c04a90c19860e2719b2b95d15",
    ("Darwin", "amd64"): "9625bba4bd96117eedae49f982aba4c2f462b268dd406c9ff18186f9b1ef8afe",
    ("Linux", "arm64"): "53e770c1c3035b5a9f1bc629fce537db1f95f62b26f4ebe6e756afd701cf077c",
    ("Linux", "amd64"): "556d891134a3c582dc1e1a3f7335fd55142e5965769855a00b944e13e48302fc",
}


def fetch(url: str, destination: Path, checksum: str, *, executable: bool = False) -> None:
    if destination.exists() and hashlib.sha256(destination.read_bytes()).hexdigest() == checksum:
        if executable:
            destination.chmod(0o755)
        return
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as download:
        temporary = Path(download.name)
        try:
            with httpx2.stream("GET", url, timeout=60, follow_redirects=True) as response:
                response.raise_for_status()
                for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                    download.write(chunk)
            download.close()
            if hashlib.sha256(temporary.read_bytes()).hexdigest() != checksum:
                raise RuntimeError(f"Checksum mismatch for {destination.name}")
            temporary.chmod(0o755 if executable else 0o644)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)


def main() -> None:
    machine = platform.machine().lower()
    arch = {"x86_64": "amd64", "aarch64": "arm64"}.get(machine, machine)
    system = platform.system()
    checksum = TOXIPROXY_HASHES.get((system, arch))
    if checksum is None:
        raise RuntimeError(f"Unsupported test-tool platform: {system}/{arch}")
    TOOLS.mkdir(parents=True, exist_ok=True)
    fetch(
        f"https://repo.maven.apache.org/maven2/org/wiremock/wiremock-standalone/"
        f"{WIREMOCK_VERSION}/wiremock-standalone-{WIREMOCK_VERSION}.jar",
        TOOLS / f"wiremock-{WIREMOCK_VERSION}.jar",
        "d097b19bd483c5038479b13a5c71e9faf8f2f5106584f0c120a7770ab0bdb367",
    )
    fetch(
        f"https://github.com/Shopify/toxiproxy/releases/download/v{TOXIPROXY_VERSION}/"
        f"toxiproxy-server-{system.lower()}-{arch}",
        TOOLS / f"toxiproxy-{TOXIPROXY_VERSION}",
        checksum,
        executable=True,
    )
    print(
        f"Databricks test tools ready: WireMock {WIREMOCK_VERSION}, Toxiproxy {TOXIPROXY_VERSION}"
    )


if __name__ == "__main__":
    main()
