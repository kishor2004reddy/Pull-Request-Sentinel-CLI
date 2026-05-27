import fnmatch
import posixpath

_ALL = frozenset({"security", "quality", "performance", "testing"})
_SEC: frozenset[str] = frozenset({"security"})
_SEC_QUAL: frozenset[str] = frozenset({"security", "quality"})
_QUAL: frozenset[str] = frozenset({"quality"})
_NO_PERF: frozenset[str] = frozenset({"security", "quality", "testing"})
_SEC_QUAL_PERF: frozenset[str] = frozenset({"security", "quality", "performance"})
_NONE: frozenset[str] = frozenset()

# Checked before extension lookup — most specific patterns first.
# Uses basename only (no directory component).
_FILENAME_PATTERNS: list[tuple[str, frozenset[str]]] = [
    ("appsettings*.json", _SEC_QUAL),
    ("Dockerfile", _SEC_QUAL),
    ("*.dockerfile", _SEC_QUAL),
    ("*Test*.cs", _NO_PERF),   # covers *Test.cs, *Tests.cs, *TestBase.cs, etc.
    ("*Spec.cs", _NO_PERF),
    ("*Fixture.cs", _NO_PERF),
]

_EXTENSION_MAP: dict[str, frozenset[str]] = {
    # C# source
    ".cs": _ALL,
    # Web / Frontend
    ".cshtml": _SEC_QUAL,
    ".razor": _SEC_QUAL,
    ".html": _SEC_QUAL,
    ".htm": _SEC_QUAL,
    ".js": _ALL,
    ".ts": _ALL,
    ".css": _QUAL,
    ".scss": _QUAL,
    ".less": _QUAL,
    ".svg": _SEC,           # SVG is text XML and can embed <script> tags
    # Project / Build
    ".csproj": _SEC_QUAL,
    ".props": _SEC_QUAL,
    ".targets": _SEC_QUAL,
    ".sln": _NONE,
    # Config
    ".yml": _SEC_QUAL,
    ".yaml": _SEC_QUAL,
    ".xml": _SEC_QUAL,
    ".json": _SEC_QUAL,
    # Infrastructure as Code
    ".bicep": _SEC_QUAL,
    ".tf": _SEC_QUAL,
    ".tfvars": _SEC_QUAL,
    # Database — Performance is relevant here (missing indexes, slow queries)
    ".sql": _SEC_QUAL_PERF,
    # Data files — PII / secrets can appear in seed / test data
    ".csv": _SEC,
    ".tsv": _SEC,
    ".resx": _SEC,
    # Documentation — secrets are commonly committed here by accident
    ".md": _SEC,
    ".txt": _SEC,
    ".rst": _SEC,
    # API / HTTP test files
    ".http": _SEC,
    ".rest": _SEC,
    # Protobuf
    ".proto": _SEC_QUAL,
}


def _agents_for_file(path: str) -> frozenset[str]:
    name = posixpath.basename(path.replace("\\", "/"))

    for pattern, agents in _FILENAME_PATTERNS:
        if fnmatch.fnmatch(name, pattern):
            return agents

    ext = posixpath.splitext(name)[1].lower()
    return _EXTENSION_MAP.get(ext, _ALL)  # unknown extension → run everything


def files_for_agent(files: list[dict], agent_key: str) -> list[dict]:
    """Return the subset of files that the given agent should review.

    File order is preserved. Files whose extension is unknown are included
    so nothing is silently skipped.
    """
    return [f for f in files if agent_key in _agents_for_file(f["filePath"])]
