"""Content hashing helpers used to bind evidence to exact inputs."""
from __future__ import annotations

import hashlib
import json
import os
from typing import Mapping

CHUNK = 1 << 20


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | os.PathLike[str]) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def fingerprint(path: str | os.PathLike[str]) -> dict:
    """Return ``{path, sha256, size}`` for an existing file."""
    p = str(path)
    return {"path": p, "sha256": sha256_file(p), "size": os.path.getsize(p)}


def hash_tree(root: str | os.PathLike[str], patterns: tuple[str, ...] = ()) -> dict[str, str]:
    """Hash every file under *root* (optionally filtered by substring patterns).

    Returns ``{relative_path: sha256}``.  Missing root -> empty dict.
    """
    root = str(root)
    out: dict[str, str] = {}
    if not os.path.isdir(root):
        return out
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if patterns and not any(pt in rel for pt in patterns):
                continue
            try:
                out[rel] = sha256_file(full)
            except OSError:  # unreadable file: record as missing
                out[rel] = "<unreadable>"
    return out


def hash_mapping(paths: Mapping[str, str]) -> dict[str, str]:
    """Hash ``{label: path}``; missing files map to ``<missing>``."""
    out: dict[str, str] = {}
    for label, path in paths.items():
        try:
            out[label] = sha256_file(path)
        except OSError:
            out[label] = "<missing>"
    return out


def stable_json_hash(obj: object) -> str:
    payload = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)
    return sha256_bytes(payload.encode("utf-8"))
