"""Build a portable 64-bit Windows ZIP with Python, Tk and dependencies included.

Run with 64-bit Windows Python after installing tools/requirements-windows-build.txt.
The output needs no installer or separately installed Python on the destination.
"""

import argparse
import hashlib
from pathlib import Path
import struct
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=ROOT / "binaries" / "PortfolioPlanner-windows-x86_64.zip")
    args = parser.parse_args(argv)
    if sys.platform != "win32" or struct.calcsize("P") != 8:
        parser.error("Build with 64-bit Windows Python and Tkinter installed.")
    build = ROOT / "build" / "windows"
    build.mkdir(parents=True, exist_ok=True)
    icon = build / "portfolio-planner.ico"
    _write_icon(ROOT / "assets" / "portfolio-planner.png", icon)
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm", "--onedir",
        "--windowed", "--noupx", "--name", "PortfolioPlanner", "--icon", str(icon),
        "--distpath", str(build / "frozen"), "--workpath", str(build / "pyinstaller"),
        "--specpath", str(build), "--add-data", f"{ROOT / 'assets'};assets",
        "--collect-all", "highspy", "--collect-data", "yfinance", str(ROOT / "main.py"),
    ], cwd=ROOT, check=True)
    frozen = build / "frozen" / "PortfolioPlanner"
    (frozen / "README.txt").write_text(
        "Portfolio planner - portable Windows edition\n\n"
        "1. Extract the entire ZIP to a folder you can write to.\n"
        "2. Double-click PortfolioPlanner.exe. Keep the _internal folder beside it.\n"
        "3. Choose a portfolio folder when prompted. You can choose an existing\n"
        "   portfolio folder or create a new, empty one.\n\n"
        "No installation, administrator access, or separate Python is required.\n"
        "Your portfolio is stored in the folder you select, separately from the app.\n"
        "This archive contains no personal portfolio data.\n"
        "Internet access is required when refreshing online market data.\n\n"
        "Requires 64-bit Windows 10 or Windows 11.\n",
        encoding="utf-8")
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED,
                             compresslevel=6) as archive:
            for path in sorted(frozen.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(frozen.parent))
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    with output.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    output.with_suffix(".zip.sha256").write_text(f"{digest}  {output.name}\n", encoding="utf-8")
    print(f"Built {output}")


def _write_icon(source, destination):
    """Wrap the app's PNG in the Windows ICO container without extra dependencies."""
    png = source.read_bytes()
    if png[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("The application icon must be a PNG image.")
    width, height = struct.unpack(">II", png[16:24])
    if not (1 <= width <= 256 and 1 <= height <= 256):
        raise ValueError("Windows icon dimensions must be between 1 and 256 pixels.")
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", width % 256, height % 256, 0, 0,
                        1, 32, len(png), 22)
    destination.write_bytes(header + entry + png)


if __name__ == "__main__":
    main()
