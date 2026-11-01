"""Download the Docling models the worker uses into one folder.

The worker image runs this at BUILD time, so a container never downloads anything on its first
request (and runs with HF_HUB_OFFLINE=1 to make sure). Usage:

    python app/parsing/download_models.py /opt/docling-models
"""

import sys
from pathlib import Path

from docling.utils.model_downloader import download_models


def main(target: str) -> None:
    download_models(
        Path(target),
        with_layout=True,  # finds paragraphs, headings, tables on the page
        with_tableformer=True,  # reads table structure
        with_rapidocr=True,  # OCR for pages without a text layer
        with_code_formula=False,  # not used: legal documents
        with_picture_classifier=False,  # not used
    )


if __name__ == "__main__":
    main(sys.argv[1])
