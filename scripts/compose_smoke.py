"""End-to-end smoke test against a running stack (`docker compose up`). Standard library only,
so CI can run it with the runner's own Python.

Uploads a digital fixture PDF, waits until the worker has parsed it, then uploads it again and
checks it comes back as a duplicate of the same document.

    python scripts/compose_smoke.py                     # port from API_PORT in .env, else 8000
    python scripts/compose_smoke.py --api http://localhost:8105
"""

import argparse
import json
import sys
import time
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "backend/tests/fixtures/syn_00001.pdf"


def default_api() -> str:
    port = "8000"
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("API_PORT="):
                port = line.split("=", 1)[1].strip() or port
    return f"http://localhost:{port}"


def upload(api: str, path: Path) -> tuple[int, dict]:
    boundary = uuid.uuid4().hex
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
    )
    body = head.encode() + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"{api}/documents",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status, json.load(resp)


def get(api: str, doc_id: str) -> dict:
    with urllib.request.urlopen(f"{api}/documents/{doc_id}", timeout=10) as resp:
        return json.load(resp)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default=default_api())
    parser.add_argument("--timeout", type=float, default=240)
    args = parser.parse_args()

    status, doc = upload(args.api, FIXTURE)
    print(f"upload -> {status}, document {doc['id']}, status {doc['status']}")
    if status not in (200, 201):
        return 1

    deadline = time.monotonic() + args.timeout
    while doc["status"] not in ("parsed", "failed"):
        if time.monotonic() > deadline:
            print(f"timed out waiting for parsing (last status: {doc['status']})")
            return 1
        time.sleep(2)
        doc = get(args.api, doc["id"])
    print(f"status {doc['status']}: {doc['page_count']} page(s), {doc['block_count']} blocks")
    if doc["status"] != "parsed" or doc["block_count"] == 0:
        print(f"error: {doc['error']}")
        return 1

    status, again = upload(args.api, FIXTURE)
    print(f"re-upload -> {status}, duplicate={again['duplicate']}")
    if status != 200 or not again["duplicate"] or again["id"] != doc["id"]:
        return 1
    print("smoke test passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
