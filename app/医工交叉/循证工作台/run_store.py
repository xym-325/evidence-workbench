"""Persist one immutable run identity and its current review/verification state."""
import json
import os
import re
import tempfile
import uuid
from pathlib import Path


def new_id():
    return uuid.uuid4().hex


def directory(root, run_id):
    if not re.fullmatch(r"[a-f0-9]{32}", str(run_id)):
        raise ValueError("无效运行编号")
    return Path(root) / run_id


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".save-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save(root, run_id, snapshot):
    write_json(directory(root, run_id) / "snapshot.json", snapshot)


def load(root, run_id):
    with (directory(root, run_id) / "snapshot.json").open(encoding="utf-8") as stream:
        return json.load(stream)
