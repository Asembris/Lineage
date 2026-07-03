"""Build the certifier Lambda deployment zip — manylinux wheels, no Docker.

`pip install --platform manylinux2014_x86_64 --only-binary=:all:` pulls the SAME Linux
binaries the public.ecr.aws/lambda/python:3.12 image would bake in, without Windows Docker's
volume-mount fragility (Docker is the documented fallback if a wheel ever refuses to resolve).

Packs: psycopg[binary] (manylinux) + certifi, then the flat handler.py + certificate.py.
boto3 is NOT packed — it ships in the Lambda runtime. Output: lambda/certifier/certifier.zip.

Run:  .venv/Scripts/python.exe lambda/certifier/build.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PKG = HERE / "package"
ZIP = HERE / "certifier.zip"

# Pinned to match requirements.txt so the Lambda and app run identical driver versions.
DEPS = ["psycopg[binary]==3.2.3", "certifi==2026.6.17"]
PLATFORM = "manylinux2014_x86_64"
PYVER = "3.12"


def main() -> int:
    if PKG.exists():
        shutil.rmtree(PKG)
    PKG.mkdir(parents=True)
    ZIP.unlink(missing_ok=True)

    print(f"[build] pip install ({PLATFORM}, cp{PYVER.replace('.', '')}) -> {PKG}")
    subprocess.run(
        [
            sys.executable, "-m", "pip", "install",
            "--target", str(PKG),
            "--platform", PLATFORM,
            "--implementation", "cp",
            "--python-version", PYVER,
            "--only-binary=:all:",
            "--upgrade",
            *DEPS,
        ],
        check=True,
    )

    # Flat modules the handler imports directly.
    shutil.copy2(HERE / "handler.py", PKG / "handler.py")
    shutil.copy2(ROOT / "app" / "services" / "certificate.py", PKG / "certificate.py")

    print(f"[build] zipping -> {ZIP}")
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(PKG.rglob("*")):
            if "__pycache__" in p.parts or p.suffix == ".pyc":
                continue
            if p.is_file():
                z.write(p, p.relative_to(PKG).as_posix())  # forward-slash arcnames

    size_mb = ZIP.stat().st_size / 1_048_576
    print(f"[build] done: {ZIP.name} = {size_mb:.1f} MB")
    # Sanity: the manylinux binary must be present (proves we got Linux, not Windows, wheels).
    sos = [p.name for p in PKG.rglob("*.so")][:3]
    print(f"[build] linux .so present: {sos if sos else 'NONE — WRONG WHEELS'}")
    return 0 if sos else 1


if __name__ == "__main__":
    raise SystemExit(main())
