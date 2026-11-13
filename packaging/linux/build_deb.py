"""Build a Debian package without dpkg-deb (works on Windows and Linux).

Layout of the installed package:

    /opt/pdf-multisign/            application + bundled Python dependencies
    /usr/bin/pdf-multisign         launcher
    /usr/share/applications/pdf-multisign.desktop
    /usr/share/icons/hicolor/256x256/apps/pdf-multisign.png

Usage:  python packaging/linux/build_deb.py [--version 1.0.0] [--out dist]
"""
from __future__ import annotations

import argparse
import gzip
import io
import os
import shutil
import tarfile
import time
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
APP_NAME = "pdf-multisign"
INSTALL_DIR = "opt/pdf-multisign"

DESKTOP_ENTRY = """[Desktop Entry]
Type=Application
Name=PDF Мультиподпись
Name[en]=PDF Multi-Signature
GenericName=Подпись PDF
Comment=Несколько подписей CryptoPro в одном PDF
Comment[en]=Multiple CryptoPro signatures in one PDF
Exec=pdf-multisign %f
Icon=pdf-multisign
Terminal=false
Categories=Office;Utility;
MimeType=application/pdf;
Keywords=pdf;signature;cryptopro;эп;подпись;
StartupWMClass=pdf-multisign
"""

LAUNCHER = """#!/bin/sh
set -e
APP_DIR=/opt/pdf-multisign
export PYTHONPATH="$APP_DIR:$APP_DIR/vendor${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONIOENCODING=utf-8
cd "$APP_DIR"
exec python3 -m pdfsign.app "$@"
"""

CONTROL = """Package: {name}
Version: {version}
Section: office
Priority: optional
Architecture: amd64
Maintainer: Петровский Дмитрий <tripswarlock@gmail.com>
Installed-Size: {size}
Depends: python3 (>= 3.10), libgl1, libxkbcommon0, libglib2.0-0, libfontconfig1, libdbus-1-3
Recommends: cprocsp-cptools
Description: Подпись PDF несколькими подписями CryptoPro
 PDF Мультиподпись подписывает PDF сразу несколькими подписями CryptoPro CSP
 за один проход, добавляя каждую подпись отдельной ревизией файла. Интерфейс
 на PySide6 в стиле Material Design, видимый блок подписи, проверка подписи
 через CryptoPro и независимый ГОСТ-хеш.
 .
 Для подписи требуется установленный CryptoPro CSP и сертификат с закрытым
 ключом в хранилище пользователя.
"""


def _iter_sources():
    yield ROOT / "pdfsign", "pdfsign"
    yield ROOT / "vendor-linux", "vendor"
    yield ROOT / "resources", "resources"
    yield ROOT / "bootstrap.py", "bootstrap.py"
    yield ROOT / "README.md", "README.md"
    yield ROOT / "LICENSE", "LICENSE"


def _add_dir(archive: tarfile.TarFile, arcname: str, mode: int = 0o755) -> None:
    info = tarfile.TarInfo(arcname.rstrip("/") + "/")
    info.type = tarfile.DIRTYPE
    info.mode = mode
    info.uid = 0
    info.gid = 0
    info.uname = "root"
    info.gname = "root"
    info.mtime = 1700000000
    archive.addfile(info)


def _add_tree(archive: tarfile.TarFile, source: Path, arc_prefix: str) -> None:
    # dpkg needs explicit directory entries, otherwise it cannot create the
    # destination tree and aborts with "no such file or directory".
    directories = [source]
    directories.extend(sorted(p for p in source.rglob("*") if p.is_dir()))
    for directory in directories:
        if "__pycache__" in directory.parts:
            continue
        relative = "" if directory == source else "/" + directory.relative_to(source).as_posix()
        _add_dir(archive, f"{arc_prefix}{relative}")
    for path in sorted(source.rglob("*")):
        if path.is_dir():
            continue
        if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        arcname = f"{arc_prefix}/{path.relative_to(source).as_posix()}"
        info = archive.gettarinfo(str(path), arcname)
        info.uid = 0
        info.gid = 0
        info.uname = "root"
        info.gname = "root"
        info.mtime = 1700000000
        with path.open("rb") as handle:
            archive.addfile(info, handle)


def _add_file(archive: tarfile.TarFile, content: bytes, arcname: str, mode: int = 0o644) -> None:
    info = tarfile.TarInfo(arcname)
    info.size = len(content)
    info.mode = mode
    info.uid = 0
    info.gid = 0
    info.uname = "root"
    info.gname = "root"
    info.mtime = 1700000000
    archive.addfile(info, io.BytesIO(content))


def _targz(files_writer) -> bytes:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.GNU_FORMAT) as archive:
        files_writer(archive)
    compressed = gzip.compress(raw.getvalue(), 6, mtime=1700000000)
    return compressed


def _ar(members: list[tuple[str, bytes]]) -> bytes:
    """Minimal ar archive writer (Debian package container)."""
    output = io.BytesIO()
    output.write(b"!<arch>\n")
    for name, data in members:
        header = f"{name:<16}{1700000000:<12}{0:<6}{0:<6}{100644:<8}{len(data):<10}`\n"
        output.write(header.encode("ascii"))
        output.write(data)
        if len(data) % 2:
            output.write(b"\n")
    return output.getvalue()


def build(version: str, out_dir: Path) -> Path:
    vendor = ROOT / "vendor-linux"
    if not vendor.is_dir():
        raise SystemExit("нет vendor-linux: сначала запустите packaging/fetch_linux_vendor.py")
    icon = ROOT / "resources" / "pdf-multisign.png"
    if not icon.exists():
        icon = ROOT / "resources" / "app.png"
    if not icon.exists():
        raise SystemExit("нет иконки resources/pdf-multisign.png")

    def data_writer(archive: tarfile.TarFile) -> None:
        for directory in ("./opt", f"./{INSTALL_DIR}", "./usr", "./usr/bin", "./usr/share",
                          "./usr/share/applications",
                          "./usr/share/icons", "./usr/share/icons/hicolor",
                          "./usr/share/icons/hicolor/256x256",
                          "./usr/share/icons/hicolor/256x256/apps"):
            _add_dir(archive, directory)
        for source, prefix in _iter_sources():
            if not source.exists():
                continue
            if source.is_dir():
                _add_tree(archive, source, f"./{INSTALL_DIR}/{prefix}")
            else:
                _add_file(
                    archive,
                    source.read_bytes(),
                    f"./{INSTALL_DIR}/{prefix}",
                )
        _add_file(
            archive,
            LAUNCHER.encode("utf-8"),
            "./usr/bin/pdf-multisign",
            mode=0o755,
        )
        _add_file(
            archive,
            DESKTOP_ENTRY.encode("utf-8"),
            "./usr/share/applications/pdf-multisign.desktop",
        )
        _add_file(
            archive,
            icon.read_bytes(),
            "./usr/share/icons/hicolor/256x256/apps/pdf-multisign.png",
        )

    data_tar = _targz(data_writer)

    installed_size = 0
    for source, _ in _iter_sources():
        if source.is_file():
            installed_size += source.stat().st_size
        elif source.is_dir():
            installed_size += sum(p.stat().st_size for p in source.rglob("*") if p.is_file())
    installed_size = installed_size // 1024 + 64

    control_text = CONTROL.format(name=APP_NAME, version=version, size=installed_size)

    def control_writer(archive: tarfile.TarFile) -> None:
        _add_file(archive, control_text.encode("utf-8"), "./control")
        _add_file(archive, b"", "./md5sums")

    control_tar = _targz(control_writer)
    debian_binary = b"2.0\n"
    members = [
        ("debian-binary", debian_binary),
        ("control.tar.gz", control_tar),
        ("data.tar.gz", data_tar),
    ]
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{APP_NAME}_{version}_amd64.deb"
    target.write_bytes(_ar(members))
    return target


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default="1.0.0")
    parser.add_argument("--out", default="dist")
    args = parser.parse_args()
    out_dir = (ROOT / args.out) if not os.path.isabs(args.out) else Path(args.out)
    target = build(args.version, out_dir)
    print(f"собран {target} ({target.stat().st_size / 1024 / 1024:.1f} МБ)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
