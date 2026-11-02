"""Download the Docling models the worker uses into one folder.

The worker image runs this at BUILD time, so a container never downloads anything on its first
request (and runs with HF_HUB_OFFLINE=1 to make sure). Usage:

    python app/parsing/download_models.py /opt/docling-models
"""

import sys
from pathlib import Path

# Real model weights are megabytes. A download that fails upstream can leave a tiny JSON error
# message saved under the model's name (seen with the RapidOCR host); the build must fail then,
# instead of shipping an image whose OCR crashes on the first scanned page.
MIN_MODEL_BYTES = 10_000
MODEL_SUFFIXES = {".onnx", ".pth", ".safetensors", ".bin", ".pt"}


def check_models(target: Path) -> None:
    files = [p for p in target.rglob("*") if p.is_file() and p.suffix in MODEL_SUFFIXES]
    if not files:
        raise SystemExit(f"no model files under {target}")
    broken = [p for p in files if p.stat().st_size < MIN_MODEL_BYTES]
    if broken:
        details = "\n".join(
            f"  {p} ({p.stat().st_size} bytes): {p.read_bytes()[:120]!r}" for p in broken
        )
        raise SystemExit(f"model downloads look broken (too small to be weights):\n{details}")


def main(target: str) -> None:
    from docling.utils.model_downloader import download_models

    download_models(
        Path(target),
        with_layout=True,  # finds paragraphs, headings, tables on the page
        with_tableformer=True,  # reads table structure
        with_rapidocr=True,  # OCR for pages without a text layer
        with_code_formula=False,  # not used: legal documents
        with_picture_classifier=False,  # not used
    )
    check_models(Path(target))


if __name__ == "__main__":
    main(sys.argv[1])
