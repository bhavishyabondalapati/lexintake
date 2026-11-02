"""Upload every synthetic document to the running stack and wait until the worker has parsed
them (free: no API calls). Writes data/eval/ids.json (manifest doc_id -> document uuid).

    PYTHONPATH=backend:. uv run --project backend python -m scripts.eval_ingest
"""

import json

from scripts.extract_preview import ROOT, SYNTHETIC, _env, upload_and_parse

OUT = ROOT / "data" / "eval" / "ids.json"


def main() -> None:
    docs = json.loads((SYNTHETIC / "manifest.json").read_text())["documents"]
    sample = [d | {"path": str(SYNTHETIC / d["file"])} for d in docs]
    api = f"http://localhost:{_env().get('API_PORT') or 8000}"
    ids = upload_and_parse(sample, api, timeout=4 * 3600)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({k: str(v) for k, v in ids.items()}, indent=1))
    print(f"{len(ids)} documents parsed; ids in {OUT}")


if __name__ == "__main__":
    main()
