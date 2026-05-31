import fnmatch
import posixpath
import re
from pathlib import Path

from pr_sentinel.config import DEFAULT_MAX_FILE_SIZE, NOISE_PATTERNS

_DIFF_HEADER = re.compile(r"^diff --git a/(.+?) b/(.+?)$", re.MULTILINE)
_TRUNCATION_NOTE = "\n\n[... diff truncated by pr-sentinel: exceeded --max-file-size ...]\n"
_GIT_META = re.compile(
    r"^(?:diff --git [^\n]+|index [0-9a-f]+\.\.[0-9a-f][^\n]*|--- [^\n]+|\+\+\+ [^\n]+)\n",
    re.MULTILINE,
)


def all_paths(raw_diff: str) -> list[str]:
    """Return every file path mentioned in the diff, before any filtering."""
    return [m.group(2) for m in _DIFF_HEADER.finditer(raw_diff)]


def load_ignore_file(path: Path) -> list[str]:
    """Read a .prsentinelignore-style file: one glob per line, # comments, blank lines skipped."""
    if not path.is_file():
        return []
    patterns: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        patterns.append(stripped)
    return patterns


def _matches_any(path: str, patterns: list[str]) -> bool:
    norm = path.replace("\\", "/")
    name = posixpath.basename(norm)
    return any(
        fnmatch.fnmatch(norm, pat) or fnmatch.fnmatch(name, pat)
        for pat in patterns
    )


def _strip_git_meta(chunk: str) -> str:
    """Remove redundant git header lines that precede the first hunk.

    `diff --git`, `index`, `--- a/`, `+++ b/` lines are fully covered by the
    FILE: header that format_diff_block prepends — stripping them cuts input
    tokens without losing any information the agents need.
    `new file mode`, `deleted file mode`, `rename from/to` lines are preserved
    because they aren't matched by _GIT_META.
    """
    first_hunk = chunk.find("\n@@")
    if first_hunk == -1:
        return chunk
    header = _GIT_META.sub("", chunk[: first_hunk + 1])
    return header + chunk[first_hunk + 1 :]


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


def parse(
    raw_diff: str,
    max_file_size: int = DEFAULT_MAX_FILE_SIZE,
    extra_skip_patterns: list[str] | None = None,
) -> list[dict]:
    """Split a unified diff into per-file records, filtering noise and user-skipped paths."""
    if not raw_diff.strip():
        return []

    matches = list(_DIFF_HEADER.finditer(raw_diff))
    if not matches:
        return []

    skip_patterns = list(NOISE_PATTERNS)
    if extra_skip_patterns:
        skip_patterns.extend(extra_skip_patterns)

    files: list[dict] = []
    for idx, match in enumerate(matches):
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(raw_diff)
        chunk = raw_diff[start:end]
        path = match.group(2)

        if _matches_any(path, skip_patterns):
            continue

        added, removed = _count_lines(chunk)
        change_type = _classify(chunk)

        chunk = _strip_git_meta(chunk)

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
