"""Build a portable x86-64 AppImage without including portfolio inputs."""

import argparse
import hashlib
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
DOWNLOADS = {
    "appimagetool.AppImage": (
        "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage",
        "95cbe7cce9717fce90c484e34052ee7c7f1d7635b33c12525b4776826a7d29b6"),
    "runtime-x86_64": (
        "https://github.com/AppImage/type2-runtime/releases/download/continuous/runtime-x86_64",
        "156f4bdbde9c52d01814600013e0a273f0118dc2de98975f3c8c63427ec79074"),
}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "appimage" / "PortfolioPlanner-x86_64.AppImage")
    args = parser.parse_args(argv)
    if sys.platform != "linux" or platform.machine() != "x86_64":
        parser.error("Build on x86-64 Linux with Python and Tkinter installed.")
    build = ROOT / "build"
    build.mkdir(exist_ok=True)
    for name, (url, digest) in DOWNLOADS.items():
        _download(build / name, url, digest)
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm", "--onedir",
        "--name", "portfolio-planner", "--distpath", str(build / "frozen"),
        "--workpath", str(build / "pyinstaller"), "--specpath", str(build),
        "--add-data", f"{ROOT / 'assets'}:assets", "--collect-all", "highspy",
        "--collect-data", "yfinance", str(ROOT / "main.py"),
    ], cwd=ROOT, check=True)
    appdir = build / "PortfolioPlanner.AppDir"
    if appdir.exists():
        shutil.rmtree(appdir)
    shutil.copytree(build / "frozen" / "portfolio-planner", appdir)
    (appdir / "portfolio-planner").rename(appdir / "AppRun")
    shutil.copy2(ROOT / "assets" / "portfolio-planner.png", appdir)
    (appdir / ".DirIcon").symlink_to("portfolio-planner.png")
    (appdir / "portfolio-planner.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=Portfolio planner\n"
        "Comment=Plan portfolio allocations and trades\nExec=AppRun\n"
        "Icon=portfolio-planner\nTerminal=false\nCategories=Office;Finance;\n"
        "StartupWMClass=Portfolioplanner\n", encoding="utf-8")
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        str(build / "appimagetool.AppImage"), "--appimage-extract-and-run",
        "--no-appstream", "--runtime-file", str(build / "runtime-x86_64"),
        str(appdir), str(output),
    ], env={**os.environ, "ARCH": "x86_64"}, check=True)
    output.chmod(0o755)
    with output.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    output.with_suffix(".AppImage.sha256").write_text(
        f"{digest}  {output.name}\n",
        encoding="utf-8")
    _write_launcher(output)
    print(f"Built {output}")


def _write_launcher(output):
    executable = str(output).replace('\\', r'\\\\').replace('%', '%%')
    for character in ('"', '`', '$'):
        executable = executable.replace(character, '\\\\' + character)
    launcher = output.with_suffix('.desktop')
    launcher.write_text(
        '[Desktop Entry]\nType=Application\nName=Portfolio planner\n'
        f'Exec="{executable}" --appimage-extract-and-run\n'
        f'Icon={ROOT / "assets" / "portfolio-planner.png"}\n'
        'Terminal=false\nCategories=Office;Finance;\nStartupWMClass=Portfolioplanner\n',
        encoding='utf-8')
    launcher.chmod(0o755)


def _download(path, url, expected):
    if path.exists():
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() == expected:
                path.chmod(0o755)
                return
    with tempfile.TemporaryDirectory(dir=path.parent) as temporary:
        staged = Path(temporary) / path.name
        with urllib.request.urlopen(url, timeout=60) as response, staged.open("wb") as destination:
            shutil.copyfileobj(response, destination)
        with staged.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                raise ValueError(f"Checksum mismatch for {path.name}; verify the upstream release before updating its digest.")
        staged.replace(path)
    path.chmod(0o755)


if __name__ == "__main__":
    main()
