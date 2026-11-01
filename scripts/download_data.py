"""Download the public evaluation datasets into data/.

    uv run --project backend python scripts/download_data.py                  # ContractNLI
    uv run --project backend python scripts/download_data.py --include-cuad   # + CUAD

Each archive is checked against a pinned SHA-256 before it is extracted, and a dataset folder is
only marked complete (data/<name>/.complete) once extraction has finished. Re-running skips
anything already complete, so the script is safe to run as often as you like.

Uses only the standard library, so it needs no extra packages.
"""

import argparse
import hashlib
import shutil
import sys
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MARKER = ".complete"
CHUNK = 1 << 20  # 1 MiB


@dataclass(frozen=True)
class Dataset:
    name: str  # folder under data/
    url: str
    sha256: str
    archive_name: str
    strip_prefix: str  # top-level folder inside the zip, dropped on extraction
    license: str


DATASETS = {
    "contractnli": Dataset(
        name="contractnli",
        url="https://stanfordnlp.github.io/contract-nli/resources/contract-nli.zip",
        sha256="e03fc77bbf8b53e2976a250e81d8a294bc3d5e5fb014521e477dee9340d6287b",
        archive_name="contract-nli.zip",
        strip_prefix="contract-nli/",
        license="CC BY 4.0",
    ),
    "cuad": Dataset(
        name="cuad",
        url="https://zenodo.org/records/4595826/files/CUAD_v1.zip?download=1",
        # Zenodo also publishes MD5 c38f490a984420b8a62600db401fafd5 for this file.
        sha256="88b694d99007d39777fa44cd72daf8297773d285dc3eab0091ba32078888d18e",
        archive_name="CUAD_v1.zip",
        strip_prefix="CUAD_v1/",
        license="CC BY 4.0",
    ),
}


class ChecksumError(Exception):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, dest: Path) -> None:
    """Stream url to dest, printing progress to stderr."""
    with urllib.request.urlopen(url) as response, dest.open("wb") as out:
        total = int(response.headers.get("Content-Length") or 0)
        done, last_shown = 0, -1
        while chunk := response.read(CHUNK):
            out.write(chunk)
            done += len(chunk)
            if total:
                percent = done * 100 // total
                if percent // 10 != last_shown:
                    last_shown = percent // 10
                    print(
                        f"  {percent:3d}%  {done / 1e6:.1f} / {total / 1e6:.1f} MB", file=sys.stderr
                    )


def safe_extract(archive: Path, dest: Path, strip_prefix: str = "") -> None:
    """Extract archive into dest, refusing any entry that would land outside dest ("zip slip")."""
    dest_root = dest.resolve()
    with zipfile.ZipFile(archive) as zf:
        for member in zf.infolist():
            name = member.filename
            if strip_prefix and name.startswith(strip_prefix):
                name = name[len(strip_prefix) :]
            if not name:
                continue
            target = (dest_root / name).resolve()
            if not target.is_relative_to(dest_root):
                raise ValueError(f"unsafe path in archive: {member.filename!r}")
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out)


def fetch(dataset: Dataset, data_dir: Path, force: bool = False) -> str:
    """Make sure data_dir/<name> holds the verified, extracted dataset.

    Returns what happened: "skipped", "extracted" (archive already downloaded) or "downloaded".
    """
    target = data_dir / dataset.name
    downloads = data_dir / "downloads"
    archive = downloads / dataset.archive_name

    if force:
        shutil.rmtree(target, ignore_errors=True)
        archive.unlink(missing_ok=True)
    elif (target / MARKER).exists():
        return "skipped"

    downloads.mkdir(parents=True, exist_ok=True)
    status = "extracted"
    if not archive.exists() or sha256_file(archive) != dataset.sha256:
        status = "downloaded"
        partial = archive.with_name(archive.name + ".part")
        print(f"Downloading {dataset.name} from {dataset.url}", file=sys.stderr)
        download(dataset.url, partial)
        actual = sha256_file(partial)
        if actual != dataset.sha256:
            partial.unlink()
            raise ChecksumError(
                f"{dataset.name}: SHA-256 mismatch (expected {dataset.sha256}, got {actual}). "
                "The download was deleted. The file may have changed upstream."
            )
        partial.replace(archive)

    # Extract into a scratch folder and rename at the end, so a crash never leaves a
    # half-extracted folder that looks complete.
    staging = data_dir / f".{dataset.name}.staging"
    shutil.rmtree(staging, ignore_errors=True)
    safe_extract(archive, staging, dataset.strip_prefix)
    (staging / MARKER).write_text(dataset.sha256 + "\n")
    shutil.rmtree(target, ignore_errors=True)
    staging.rename(target)
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--include-cuad", action="store_true", help="also download CUAD (106 MB)")
    parser.add_argument("--force", action="store_true", help="re-download and re-extract")
    parser.add_argument("--data-dir", type=Path, default=REPO_ROOT / "data")
    args = parser.parse_args(argv)

    names = ["contractnli"] + (["cuad"] if args.include_cuad else [])
    for name in names:
        dataset = DATASETS[name]
        try:
            status = fetch(dataset, args.data_dir, force=args.force)
        except ChecksumError as error:
            print(f"ERROR: {error}", file=sys.stderr)
            return 1
        where = args.data_dir / name
        if status == "skipped":
            print(f"{name}: already present in {where}, skipping")
        else:
            print(f"{name}: verified and extracted to {where} ({dataset.license})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
