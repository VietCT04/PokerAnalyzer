"""Keep original import inputs and their order independently of SQLite."""

import hashlib
import json
import os
import re
from pathlib import Path
from tempfile import NamedTemporaryFile


BATCH_NAME = re.compile(r"([0-9]{12})(?:\.failed)?\.json")
EXTENSIONS = {".csv", ".zip"}
CHUNK_SIZE = 1024 * 1024


def raw_dir(db_path):
    return db_path.with_suffix(".raw")


def checksum(path, destination=None):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(CHUNK_SIZE), b""):
            digest.update(chunk)
            if destination is not None:
                destination.write(chunk)
    return digest.hexdigest()


def store_input(root, path):
    extension = path.suffix.lower()
    if extension not in EXTENSIONS:
        raise ValueError(f"{path.name}: expected a ZIP or CSV file")
    blobs = root / "blobs"
    blobs.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=blobs, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            digest = checksum(path, stream)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            stream.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        destination = blobs / (digest + extension)
        if destination.exists():
            if checksum(destination) != digest:
                raise ValueError(f"Raw archive checksum mismatch for {path.name}")
        else:
            temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return {"name": path.name, "extension": extension, "sha256": digest}


def record_batch(root, files, session, member_sessions):
    """Call while holding the database write transaction to preserve import order."""
    folder = root / "batches"
    folder.mkdir(parents=True, exist_ok=True)
    numbers = [int(match[1]) for path in folder.iterdir() if (match := BATCH_NAME.fullmatch(path.name))]
    path = folder / f"{max(numbers, default=0) + 1:012}.json"
    batch = {
        "version": 1,
        "files": [store_input(root, source) for source in files],
        "session": session,
        "member_sessions": member_sessions,
    }
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=folder, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(batch, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            stream.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return path, batch


def batch_inputs(root, batch, verify=False):
    if batch["version"] != 1:
        raise ValueError("Unsupported raw archive version")
    for item in batch["files"]:
        digest, extension = item["sha256"], item["extension"]
        if not re.fullmatch(r"[a-f0-9]{64}", digest) or extension not in EXTENSIONS:
            raise ValueError("Invalid raw archive manifest")
        path = root / "blobs" / (digest + extension)
        if verify and checksum(path) != digest:
            raise ValueError(f"Raw archive checksum mismatch for {item['name']}")
        yield path, item["name"]


def archived_batches(root):
    return sorted(path for path in (root / "batches").glob("*.json") if re.fullmatch(r"[0-9]{12}\.json", path.name))
