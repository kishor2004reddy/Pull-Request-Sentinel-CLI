import fnmatch
import re

NOISE_PATTERNS = [
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "Pipfile.lock",
    "*.min.js",
    "*.min.css",
    "*.generated.*",
    "*.Designer.cs",
    "*.g.cs",
    "*.g.i.cs",
    "bin/*",
    "*/bin/*",
    "obj/*",
    "*/obj/*",
    "dist/*",
    "*/dist/*",
    "build/*",
    "*/build/*",
    "node_modules/*",
    "*/node_modules/*",
]

_DIFF_HEADER = re.compile(r"^diff --git a/(.+?) b/(.+?)$", re.MULTILINE)
_TRUNCATION_NOTE = "\n\n[... diff truncated by pr-sentinel: exceeded --max-file-size ...]\n"


def _is_noise(path: str) -> bool:
    norm = path.replace("\\", "/")
    return any(fnmatch.fnmatch(norm, pat) for pat in NOISE_PATTERNS)


def _classify(chunk: str) -> str:
    if "\nnew file mode" in chunk:
        return "added"
    if "\ndeleted file mode" in chunk:
        return "deleted"
    if "\nrename from " in chunk:
        return "renamed"
    return "modified"


def _count_lines(chunk: str) -> tuple[int, int]:
    added = removed = 0
    for line in chunk.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return added, removed


def parse(raw_diff: str, max_file_size: int = 20000) -> list[dict]:
    """Split a unified diff into per-file records, filtering noise."""
    if not raw_diff.strip():
        return []

    matches = list(_DIFF_HEADER.finditer(raw_diff))
    if not matches:
        return []

    files: list[dict] = []
    for idx, match in enumerate(matches):
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(raw_diff)
        chunk = raw_diff[start:end]
        path = match.group(2)

        if _is_noise(path):
            continue

        added, removed = _count_lines(chunk)
        change_type = _classify(chunk)

        if len(chunk) > max_file_size:
            chunk = chunk[:max_file_size] + _TRUNCATION_NOTE

        files.append(
            {
                "filePath": path,
                "changeType": change_type,
                "diff": chunk,
                "addedLines": added,
                "removedLines": removed,
            }
        )

    return files
