"""Merge several near-simultaneous launches (one per selected file) into a single call.

Each process drops its file into a spool folder. The first one to create the lock file becomes
the leader: it waits until no new file has arrived for QUIET seconds, releases the lock, reads
the spool, and runs the tool with all files. The others exit at once.
"""
from __future__ import annotations

import os
import tempfile
import time
import uuid
from typing import Optional

QUIET = 0.6     # seconds without new arrivals before the leader proceeds
MAX_WAIT = 5.0  # never wait longer than this
STALE = 30.0    # a lock older than this is left over from a crash


def collect(tool_id: str, path: str) -> Optional[list[str]]:
    """Return the full list of files if this process is the leader, else None."""
    spool = os.path.join(tempfile.gettempdir(), f"niice-spool-{tool_id}")
    os.makedirs(spool, exist_ok=True)
    entry = os.path.join(spool, f"{time.time():.6f}-{uuid.uuid4().hex}.path")
    with open(entry + ".tmp", "w", encoding="utf-8") as f:
        f.write(os.path.abspath(path))
    os.replace(entry + ".tmp", entry)

    lock = os.path.join(spool, "lock")
    try:
        if os.path.exists(lock) and time.time() - os.path.getmtime(lock) > STALE:
            os.remove(lock)
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        return None  # someone else is collecting; they will pick our entry up

    start = time.time()
    seen = -1
    last_change = time.time()
    while time.time() - start < MAX_WAIT:
        n = len([e for e in os.listdir(spool) if e.endswith(".path")])
        if n != seen:
            seen, last_change = n, time.time()
        elif time.time() - last_change >= QUIET:
            break
        time.sleep(0.1)
    os.remove(lock)

    files = []
    for e in sorted(os.listdir(spool)):  # names start with the arrival time
        if e.endswith(".path"):
            p = os.path.join(spool, e)
            try:
                with open(p, encoding="utf-8") as f:
                    files.append(f.read().strip())
                os.remove(p)
            except OSError:
                pass
    return files or None
