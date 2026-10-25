"""Check public source/distributions without reading private development directories."""
from __future__ import annotations
import argparse
import re
import subprocess
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
DIRECTORIES = {"spc5tool", "tests", "docs", "tools", ".github"}
FILES = {"README.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "CHANGELOG.md",
         "CONTRIBUTING.md", "SECURITY.md", "pyproject.toml", "MANIFEST.in",
         "launch.py", "Start Tigard Flasher.command", "Start Tigard Flasher.cmd",
         "Setup Windows.cmd",
         "package.json", "package-lock.json", ".gitignore", ".gitattributes"}
PRIVATE_SUFFIXES = {".bin", ".hex", ".s19", ".srec", ".pcp", ".exe", ".elf",
                    ".zip", ".log", ".pem", ".key", ".pyc"}
PRIVATE_DIRS = {"research", "backups", "node_modules", "test-results", "__pycache__", ".git"}
PATTERNS = [re.compile(p) for p in (
    r"/(?:Users|home)/[A-Za-z0-9_.-]+/",
    r"\bTG[0-9a-fA-F]{6,}\b",
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    r"\bgh[pousr]_[A-Za-z0-9]{30,}\b",
    r"\bAKIA[A-Z0-9]{16}\b",
)]


def inspect(name, data):
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Unsafe archive path: {name}")
    if path.suffix.lower() in PRIVATE_SUFFIXES or any(
        p in PRIVATE_DIRS or p.startswith((".venv", ".spc5tool", ".env")) for p in path.parts
    ):
        raise ValueError(f"Private/generated file in release: {name}")
    if path.suffix != ".png":
        text = data.decode("utf-8")
        if any(pattern.search(text) for pattern in PATTERNS):
            raise ValueError(f"Private content pattern in {name}; inspect locally")


def source_files():
    if (ROOT / ".git").is_dir():
        names = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT).decode().split("\0")
        return [ROOT / name for name in names if name]
    return sorted(p for p in ROOT.rglob("*") if p.is_file() and p.name != ".DS_Store" and (
        p.relative_to(ROOT).parts[0] in DIRECTORIES or p.name in FILES and p.parent == ROOT
    ) and not any(part in {"__pycache__", "node_modules"} for part in p.parts))


def check_source():
    paths = source_files()
    names = {p.relative_to(ROOT).as_posix() for p in paths}
    if not FILES.issubset(names):
        raise ValueError(f"Missing source files: {sorted(FILES - names)}")
    for path in paths:
        name = path.relative_to(ROOT).as_posix()
        if name.split("/")[0] not in DIRECTORIES and name not in FILES:
            raise ValueError(f"Unreviewed top-level source entry: {name}")
        if path.is_symlink():
            raise ValueError(f"Release must not follow symlinks: {name}")
        data = path.read_bytes()
        inspect(name, data)
        if path.suffix == ".md":
            for link in re.findall(r"\]\(([^)]+)\)", data.decode()):
                if "://" in link or link.startswith("#"):
                    continue
                destination = (path.parent / link.split("#")[0]).resolve()
                if not destination.is_file() or not destination.is_relative_to(ROOT):
                    raise ValueError(f"Broken/nonpublic documentation link in {name}: {link}")
    print(f"Public source checks passed: {len(paths)} files; no forbidden inputs or broken local links.")


def check_archive(path):
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            entries = {name: archive.read(name) for name in archive.namelist() if not name.endswith("/")}
    else:
        with tarfile.open(path, "r:gz") as archive:
            entries = {}
            for entry in archive:
                if entry.issym() or entry.islnk():
                    raise ValueError(f"Unexpected archive link in {path.name}")
                if entry.isfile():
                    entries[entry.name] = archive.extractfile(entry).read()
    for name, data in entries.items():
        inspect(name, data)
        if path.suffix == ".whl" and (name.startswith("tests/") or name == "spc5tool/simulator.py"):
            raise ValueError(f"Test-only device model shipped in wheel: {name}")
    required = ["spc5tool/webui/index.html", "spc5tool/webui/wiring.js",
                "spc5tool/webui/assets/tigard-render.png", "spc5tool/webui/assets/jtag-14pin.svg",
                "spc5tool/webui/assets/spc560-10pin.svg", "spc5tool/webui/assets/TIGARD-LICENSE.txt"]
    for suffix in required + ["LICENSE", "THIRD_PARTY_NOTICES.md"]:
        if not any(name == suffix or name.endswith("/" + suffix) for name in entries):
            raise ValueError(f"Missing packaged asset/license in {path.name}: {suffix}")
    print(f"Distribution checks passed: {path.name}, {len(entries)} files.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path)
    args = parser.parse_args()
    check_source()
    if args.dist:
        packages = sorted([*args.dist.glob("*.whl"), *args.dist.glob("*.tar.gz")])
        if not any(p.suffix == ".whl" for p in packages) or not any(p.name.endswith(".tar.gz") for p in packages):
            raise ValueError("Both wheel and source distribution are required")
        for path in packages:
            check_archive(path)


if __name__ == "__main__":
    main()
