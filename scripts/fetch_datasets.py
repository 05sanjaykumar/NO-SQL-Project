"""
Fetch the public datasets that are downloadable without an account into data/raw/
(git-ignored) and print each file's size and sha256 for docs/datasets.md.

    python scripts/fetch_datasets.py elliptic
    python scripts/fetch_datasets.py amlsim      # clone IBM/AMLSim (build/run: docs/datasets.md)
    python scripts/fetch_datasets.py verify      # hashes of everything under data/raw

IBM AMLworld and PaySim need a Kaggle account: download them by hand
(see docs/datasets.md) into data/raw/amlworld/ and data/raw/paysim/.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"

ELLIPTIC_URL = "https://data.pyg.org/datasets/elliptic"
ELLIPTIC_FILES = (
    "elliptic_txs_features.csv.zip",
    "elliptic_txs_classes.csv.zip",
    "elliptic_txs_edgelist.csv.zip",
)
AMLSIM_REPO = "https://github.com/IBM/AMLSim.git"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def report(path: Path) -> None:
    print(
        f"{path.relative_to(ROOT).as_posix()}  {path.stat().st_size:,} bytes  sha256 {sha256(path)}"
    )


def fetch_elliptic() -> None:
    out = RAW / "elliptic"
    out.mkdir(parents=True, exist_ok=True)
    for name in ELLIPTIC_FILES:
        target = out / name
        if not target.exists():
            print(f"downloading {ELLIPTIC_URL}/{name}")
            urllib.request.urlretrieve(f"{ELLIPTIC_URL}/{name}", target)
        report(target)
        with zipfile.ZipFile(target) as z:
            for member in z.namelist():
                # Only plain CSV members, extracted flat (no paths from the archive).
                if member.endswith(".csv") and "/" not in member.strip("/") and ".." not in member:
                    (out / member).write_bytes(z.read(member))
                    report(out / member)


def fetch_amlsim() -> None:
    out = RAW / "amlsim" / "AMLSim"
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--depth", "1", AMLSIM_REPO, str(out)], check=True)
    rev = subprocess.run(
        ["git", "-C", str(out), "rev-parse", "HEAD"], capture_output=True, text=True
    )
    print(f"AMLSim at commit {rev.stdout.strip()}")


def verify() -> None:
    for p in sorted(RAW.rglob("*")):
        if p.is_file() and ".git" not in p.parts:
            report(p)


if __name__ == "__main__":
    {"elliptic": fetch_elliptic, "amlsim": fetch_amlsim, "verify": verify}[
        sys.argv[1] if len(sys.argv) > 1 else "verify"
    ]()
