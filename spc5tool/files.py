"""Backup manifests and persistent offline state (never opened as firmware)."""
import hashlib
import json
import os
import tempfile
import zipfile
from pathlib import Path


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def write_new(path, data):
    with Path(path).open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def save_backup(directory, target, originals):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    entries = []
    for block, data in originals:
        name = f"{block.address:08x}-{block.size:08x}.bin"
        write_new(directory / name, data)
        entries.append({"file": name, "address": block.address, "size": len(data), "sha256": sha256(data)})
    write_new(directory / "manifest.json", (json.dumps({"format": 1, "target": target, "blocks": entries}, indent=2) + "\n").encode())
    # Persist directory entries before the caller is allowed to erase flash.
    if os.name != "nt":
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def load_state(path, target, regions):
    if path is None or not Path(path).exists():
        return
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        if manifest != {"format": 1, "target": target, "regions": {str(b): len(d) for b, d in regions.items()}}:
            raise ValueError("simulation state does not match the target")
        loaded = {}
        for base, data in regions.items():
            info = archive.getinfo(f"{base:08x}.bin")
            if info.file_size != len(data):
                raise ValueError("simulation state region has the wrong size")
            loaded[base] = archive.read(info)
        for base, data in loaded.items():
            regions[base][:] = data


def save_state(path, target, regions):
    if path is None:
        return
    path = Path(path)
    descriptor, temporary = tempfile.mkstemp(prefix=".spc5-state-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                manifest = {"format": 1, "target": target, "regions": {str(b): len(d) for b, d in regions.items()}}
                archive.writestr("manifest.json", json.dumps(manifest))
                for base, data in regions.items():
                    archive.writestr(f"{base:08x}.bin", data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
