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


def write_version_resource(path: Path):
    """Generate the Explorer resource from the application's release version."""
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo, StringFileInfo, StringStruct, StringTable,
        VarFileInfo, VarStruct, VSVersionInfo,
    )
    parts = tuple(int(part) for part in __version__.split("."))
    if len(parts) != 3 or any(not 0 <= part <= 65535 for part in parts):
        raise ValueError("Windows release versions must have three 16-bit numeric components")
    numeric = (*parts, 0)
    fields = {
        "FileDescription": "TIFview printing image, channel and layer viewer",
        "FileVersion": __version__, "InternalName": "TIFview",
        "OriginalFilename": "TIFview.exe", "ProductName": "TIFview", "ProductVersion": __version__,
    }
    info = VSVersionInfo(
        ffi=FixedFileInfo(filevers=numeric, prodvers=numeric, mask=0x3f, flags=0,
                         OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
        kids=[StringFileInfo([StringTable("040904B0", [StringStruct(key, value) for key, value in fields.items()])]),
              VarFileInfo([VarStruct("Translation", [1033, 1200])])],
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(info), encoding="utf-8")
    return fields, numeric


def verify_version_resource(executable: Path, fields, numeric):
    """Read actual Explorer/.NET metadata from the finished PE executable."""
    command = (
        "$ErrorActionPreference='Stop'; "
        "$info=(Get-Item -LiteralPath $env:TIFVIEW_VERSION_EXE).VersionInfo; "
        "[pscustomobject]@{FileVersion=$info.FileVersion; ProductVersion=$info.ProductVersion; "
        "ProductName=$info.ProductName; FileDescription=$info.FileDescription; "
        "InternalName=$info.InternalName; OriginalFilename=$info.OriginalFilename; "
        "NumericVersion=@($info.FileMajorPart,$info.FileMinorPart,$info.FileBuildPart,$info.FilePrivatePart)} "
        "| ConvertTo-Json -Compress"
    )
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                            check=True, capture_output=True, text=True,
                            env=dict(os.environ, TIFVIEW_VERSION_EXE=str(executable)))
    actual = json.loads(result.stdout)
    if any(actual.get(key) != value for key, value in fields.items()) or tuple(actual["NumericVersion"]) != numeric:
        raise SystemExit(f"Executable version metadata did not match the release: {actual}")
    return actual


def main():
    if sys.platform != "win32" or sys.maxsize <= 2**32:
        raise SystemExit("Build with 64-bit Python on Windows.")
    tag = os.environ.get("GITHUB_REF", "")
    if tag.startswith("refs/tags/") and tag != f"refs/tags/v{__version__}":
        raise SystemExit("Release tag must match tifview.__version__.")
    if (ROOT / "README.md").read_text(encoding="utf-8").splitlines()[0] != f"# TIFview {__version__}":
        raise SystemExit("README line 1 must match the application version.")
    version_path = ROOT / "build/windows-version.txt"
    version_fields, numeric_version = write_version_resource(version_path)
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
        "--version-file", str(version_path),
        # TIFF codecs load their compiled modules dynamically.
        "--collect-all", "imagecodecs", "--copy-metadata", "imagecodecs",
        "--exclude-module", "PySide6.QtTest", "--exclude-module", "pytest",
        "--exclude-module", "tkinter", "--exclude-module", "_tkinter",
        str(ROOT / "tools/windows_entry.py"),
    ], cwd=ROOT, check=True, env=build_env)
    bundle = ROOT / "dist/TIFview"
    executable_version = verify_version_resource(bundle / "TIFview.exe", version_fields, numeric_version)
    (ROOT / "build/executable-version.json").write_text(json.dumps(executable_version, indent=2), encoding="utf-8")
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
    report.with_name(report.stem + "-layers.tif").unlink(missing_ok=True)
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
