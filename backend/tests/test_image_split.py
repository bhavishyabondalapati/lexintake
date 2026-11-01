"""The api image installs `uv sync --no-dev` (no `parse` group). Docling and torch must never
sneak into it through a main dependency, or it stops being the light image."""

import subprocess

from tests.conftest import BACKEND

HEAVY = ("torch", "docling", "onnxruntime", "transformers")


def _export(*args: str) -> set[str]:
    out = subprocess.run(
        ["uv", "export", "--frozen", "--no-hashes", "--no-emit-project", *args],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return {
        line.split("==")[0].strip().lower()
        for line in out.splitlines()
        if line and not line.startswith((" ", "#"))
    }


def test_api_image_dependencies_have_no_ml_packages():
    api = _export("--no-dev")
    assert not [p for p in api if p.startswith(HEAVY)]


def test_worker_image_dependencies_have_docling_and_torch():
    worker = _export("--no-dev", "--group", "parse")
    assert {"docling-slim", "torch"} <= worker
