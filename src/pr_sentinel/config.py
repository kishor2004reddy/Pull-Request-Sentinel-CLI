"""Centralized tunable defaults and shared constants for PR Sentinel.

Single source of truth for values that are user-facing (CLI defaults), shared
across modules, or otherwise worth tuning in one place. Module-local
implementation details (regexes, format strings used in a single file) stay
where they are used.
"""
from pathlib import Path

# --- Orchestration / claude execution ---------------------------------------
DEFAULT_MAX_PARALLEL = 12
DEFAULT_TIMEOUT = 600
DEFAULT_MODEL = "sonnet"
DEFAULT_SUMMARY_MODEL = "haiku"

# --- Diff processing --------------------------------------------------------
DEFAULT_CHUNK_BUDGET = 100_000
DEFAULT_MAX_FILE_SIZE = 20_000
DEFAULT_UNIFIED_CONTEXT = 3

# --- Agents -----------------------------------------------------------------
DEFAULT_AGENTS = ["security", "quality", "performance", "testing"]
VALID_AGENTS = set(DEFAULT_AGENTS)
VALID_SEVERITIES = {"Low", "Medium", "High"}
SEVERITY_ORDER = {"High": 0, "Medium": 1, "Low": 2}

# --- Report output ----------------------------------------------------------
VALID_FORMATS = {"json", "markdown", "both"}
DEFAULT_OUT_DIR = Path("./reports")
REPORT_JSON_FILENAME = "report.json"
REPORT_MARKDOWN_FILENAME = "review-report.md"
SOURCE_DIFF_FILENAME = "source.diff"
IGNORE_FILE_NAME = ".prsentinelignore"

# --- Noise filtering --------------------------------------------------------
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
    "*/__pycache__/*",

    # binary files that are large but unlikely to contain PR-relevant info
    "*.png", "*.jpg", "*.jpeg", "*.gif", "*.ico", "*.webp", "*.bmp", "*.tiff",
    "*.ttf", "*.woff", "*.woff2", "*.eot", "*.otf",
    "*.mp4", "*.mp3", "*.wav", "*.avi", "*.mov", "*.webm",
    "*.pdf",
    "*.dll", "*.exe", "*.pdb", "*.nupkg",
    "*.zip", "*.tar", "*.gz", "*.rar", "*.7z",
    "*.docx", "*.xlsx", "*.pptx",
]

# --- Cache ------------------------------------------------------------------
CACHE_DIR_ENV = "PR_SENTINEL_CACHE_DIR"
DEFAULT_CACHE_DIR = Path.home() / ".pr-sentinel" / "cache"
AUTO_PRUNE_AGE_DAYS = 90
AUTO_PRUNE_AGE_SECONDS = AUTO_PRUNE_AGE_DAYS * 86400

# --- CLI ---------------------------------------------------------------------
DEFAULT_BASE_BRANCH = "main"
DEFAULT_HEAD_REF = "HEAD"
DEFAULT_REPORT_FORMAT = "both"
DEFAULT_PRUNE_AGE = "30d"

RISK_STYLE = {
    "High": "red",
    "Medium": "yellow",
    "Low": "green",
    "None": "green",
    "Unknown": "red",
}
