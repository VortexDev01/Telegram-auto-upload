"""
Build a versioned Windows EXE.

    python build.py

Produces dist/AutoUpload-v<version>.exe, with the version from version.py
also written into the EXE's file properties (right-click > Properties >
Details). Older versions stay in dist/, so you can keep or roll back builds.
"""

import os
import sys

import PyInstaller.__main__

from version import __version__

APP_NAME = "AutoUpload"
ROOT = os.path.dirname(os.path.abspath(__file__))

VERSION_INFO = """\
VSVersionInfo(
  ffi=FixedFileInfo(filevers={nums}, prodvers={nums}),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', ''),
      StringStruct('FileDescription', 'Telegram Auto Uploader'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('InternalName', '{name}'),
      StringStruct('OriginalFilename', '{exe}'),
      StringStruct('ProductName', 'Telegram Auto Uploader'),
      StringStruct('ProductVersion', '{version}'),
    ])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])]),
  ],
)
"""


def _version_tuple(version: str) -> tuple[int, int, int, int]:
    parts = [int(p) for p in version.split(".")]
    if not 1 <= len(parts) <= 4:
        sys.exit(f"Invalid version '{version}' in version.py (expected e.g. 2.0.0)")
    return tuple(parts + [0] * (4 - len(parts)))


def main():
    exe_name = f"{APP_NAME}-v{__version__}"
    dist_dir = os.path.join(ROOT, "dist")
    if os.path.exists(os.path.join(dist_dir, exe_name + ".exe")):
        print(f"dist/{exe_name}.exe already exists and will be replaced. "
              "Bump __version__ in version.py to keep it.")

    build_dir = os.path.join(ROOT, "build")
    os.makedirs(build_dir, exist_ok=True)
    version_file = os.path.join(build_dir, "version_info.txt")
    with open(version_file, "w", encoding="utf-8") as f:
        f.write(VERSION_INFO.format(
            nums=_version_tuple(__version__), version=__version__,
            name=APP_NAME, exe=exe_name + ".exe",
        ))

    PyInstaller.__main__.run([
        os.path.join(ROOT, "main.py"),
        "--name", exe_name,
        "--onefile",
        "--windowed",
        "--icon", os.path.join(ROOT, "logotean.ico"),
        "--version-file", version_file,
        "--distpath", dist_dir,
        "--workpath", build_dir,
        "--specpath", build_dir,
        "--noconfirm",
        "--clean",
    ])
    print(f"\nBuilt dist/{exe_name}.exe")


if __name__ == "__main__":
    main()
