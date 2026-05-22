import hashlib
import json
import os
import shutil
import threading
import time
from pathlib import Path

_CACHE_ROOT_ENV = "PR_SENTINEL_CACHE_DIR"
_DEFAULT_ROOT = Path.home() / ".pr-sentinel" / "cache"

_stats_lock = threading.Lock()
_stats = {"hits": 0, "misses": 0}


def cache_dir() -> Path:
    """Return the active cache root. Honors $PR_SENTINEL_CACHE_DIR override."""
    override = os.environ.get(_CACHE_ROOT_ENV)
    if override:
        return Path(override)
    return _DEFAULT_ROOT


def cache_key(prompt: str, model: str | None) -> str:
    """Stable hash of (model, prompt). Any change to either invalidates the entry."""
    h = hashlib.sha256()
    h.update((model or "").encode("utf-8"))
    h.update(b"\n")
    h.update(prompt.encode("utf-8"))
    return h.hexdigest()


def _path_for(key: str) -> Path:
    return cache_dir() / key[:2] / f"{key}.json"


def get(key: str) -> dict | None:
    """Return the cached response for `key`, or None on miss / read error."""
    path = _path_for(key)
    if not path.exists():
        with _stats_lock:
            _stats["misses"] += 1
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        with _stats_lock:
            _stats["hits"] += 1
        return data
    except Exception:
        with _stats_lock:
            _stats["misses"] += 1
        return None


def set(key: str, response: dict) -> None:
    """Write `response` to the cache under `key`. Silently ignores I/O errors."""
    path = _path_for(key)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(response), encoding="utf-8")
    except Exception:
        pass


def size() -> tuple[int, int]:
    """Return (entry_count, total_bytes) across the cache directory."""
    root = cache_dir()
    if not root.exists():
        return (0, 0)
    count = 0
    bytes_ = 0
    for p in root.rglob("*.json"):
        try:
            bytes_ += p.stat().st_size
            count += 1
        except OSError:
            continue
    return (count, bytes_)


def clear() -> int:
    """Delete the cache directory entirely. Returns the count removed."""
    root = cache_dir()
    if not root.exists():
        return 0
    count, _ = size()
    try:
        shutil.rmtree(root)
    except Exception:
        return 0
    return count


def prune(max_age_seconds: int, dry_run: bool = False) -> tuple[int, int]:
    """Delete cache entries with mtime older than max_age_seconds.

    Returns (count, bytes_freed). With dry_run=True, identifies victims but
    doesn't delete them. mtime is set when the entry is written; reads do not
    refresh it, so an entry's age = "how long since it was first cached."
    """
    root = cache_dir()
    if not root.exists():
        return (0, 0)
    cutoff = time.time() - max_age_seconds
    count = 0
    bytes_ = 0
    for p in root.rglob("*.json"):
        try:
            stat = p.stat()
            if stat.st_mtime < cutoff:
                bytes_ += stat.st_size
                count += 1
                if not dry_run:
                    p.unlink()
        except OSError:
            continue
    return (count, bytes_)


def stats() -> dict:
    """Snapshot of (hits, misses) for the current process."""
    with _stats_lock:
        return dict(_stats)


def reset_stats() -> None:
    """Zero out the in-process stats counter. Call at the start of each run."""
    with _stats_lock:
        _stats["hits"] = 0
        _stats["misses"] = 0
