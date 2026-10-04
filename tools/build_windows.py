"""Build, check and zip a self-contained Windows x64 viewer."""
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

from tifview import __version__
from tools.make_demo import make_demo


ROOT = Path(__file__).resolve().parents[1]


def main():
    if sys.platform != "win32" or sys.maxsize <= 2**32:
        raise SystemExit("Build with 64-bit Python on Windows.")
    tag = os.environ.get("GITHUB_REF", "")
    if tag.startswith("refs/tags/") and tag != f"refs/tags/v{__version__}":
        raise SystemExit("Release tag must match tifview.__version__.")
    windows = Path(os.environ.get("WINDIR", "C:/Windows"))
    build_env = dict(os.environ, PYINSTALLER_CONFIG_DIR=str(ROOT / "build/pyinstaller-cache"))
    # Other tools (e.g. Poppler) can supply incompatible DLLs with the same name
    # as Qt's Windows dependencies. Resolve from Python and Windows, not host PATH.
    build_env["PATH"] = os.pathsep.join(str(path) for path in [
        Path(sys.executable).parent, Path(sys.base_prefix), Path(sys.base_prefix) / "DLLs",
        windows / "System32", windows,
    ])
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
        "--onedir", "--name", "TIFview", "--paths", str(ROOT),
        "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build/pyinstaller"),
        "--specpath", str(ROOT / "build"),
        # TIFF codecs load their compiled modules dynamically.
        "--collect-all", "imagecodecs", "--copy-metadata", "imagecodecs",
        "--exclude-module", "PySide6.QtTest", "--exclude-module", "pytest",
        "--exclude-module", "tkinter", "--exclude-module", "_tkinter",
        str(ROOT / "tools/windows_entry.py"),
    ], cwd=ROOT, check=True, env=build_env)
    bundle = ROOT / "dist/TIFview"
    shutil.copyfile(ROOT / "docs/portable-readme.txt", bundle / "START-HERE.txt")
    shutil.copyfile(ROOT / "README.md", bundle / "README.md")
    shutil.copytree(ROOT / "docs", bundle / "docs", dirs_exist_ok=True)
    # Retain dependency license notices with the redistributed runtime.
    notices = bundle / "licenses"
    for package in ["numpy", "tifffile", "imagecodecs", "psdtags", "PySide6-Essentials",
                    "shiboken6", "Pillow", "pyinstaller"]:
        dist = importlib.metadata.distribution(package)
        for file in dist.files or []:
            if any(word in str(file).lower() for word in ("license", "copying", "notice")):
                source = Path(dist.locate_file(file))
                if source.is_file():
                    destination = notices / package / str(file).replace("..", "_")
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, destination)
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        raise SystemExit("Python LICENSE.txt is required for redistribution.")
    shutil.copyfile(python_license, notices / "Python-LICENSE.txt")
    (bundle / "THIRD-PARTY.txt").write_text(
        "This build includes Python, Qt/PySide6, NumPy, tifffile, imagecodecs, psdtags, "
        "Pillow and their bundled libraries. License notices are in licenses/ and _internal/.\n"
        "Qt/PySide6 project and source: https://www.qt.io/qt-for-python\n"
        "Python license: https://docs.python.org/3/license.html\n", encoding="utf-8")
    fixture = ROOT / "build/SYNTHETIC-package-check.tif"
    report = ROOT / "build/package-check.json"
    make_demo(fixture, layers=True)
    if report.exists():
        report.unlink()
    report.with_name(report.stem + "-edited.tif").unlink(missing_ok=True)
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    # No source checkout or Python command is involved in the frozen smoke test.
    subprocess.run([str(bundle / "TIFview.exe"), "--smoke-test", str(fixture), str(report)],
                   cwd=bundle, env=env, check=True, timeout=60)
    if not json.loads(report.read_text(encoding="utf-8"))["passed"]:
        raise SystemExit("Packaged viewer check failed")
    archive = ROOT / f"dist/TIFview-{__version__}-windows-x64.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as target:
        for path in sorted(bundle.rglob("*")):
            if path.is_file():
                target.write(path, path.relative_to(bundle.parent))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".zip.sha256").write_text(f"{digest}  {archive.name}\n", encoding="ascii")
    print(f"Verified portable build: {archive} ({archive.stat().st_size / 2**20:.1f} MiB)")


if __name__ == "__main__":
    main()
