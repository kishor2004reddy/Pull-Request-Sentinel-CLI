"""Centralized tunable defaults and shared constants for PR Sentinel.

Single source of truth for values that are user-facing (CLI defaults), shared
across modules, or otherwise worth tuning in one place. Module-local
implementation details (regexes, format strings used in a single file) stay
where they are used.
"""
from pathlib import Path

# --- Orchestration / provider execution -------------------------------------
DEFAULT_MAX_PARALLEL = 12
DEFAULT_TIMEOUT = 600

DEFAULT_SUMMARY_TIMEOUT = 300

# --- Providers ---------------------------------------------------------------
# A provider is an AI CLI we shell out to. Each has its own model namespace,
# so the default model is resolved *per provider* (see default_model_for).
DEFAULT_PROVIDER = "copilot"
VALID_PROVIDERS = {"claude", "copilot"}

# Claude Code CLI defaults (shortcuts understood by `claude --model`).
DEFAULT_MODEL = "sonnet"
# Summary is a lightweight dedup pass — a fast model keeps it off the critical path.
DEFAULT_SUMMARY_MODEL = "haiku"

# GitHub Copilot CLI defaults. Copilot uses a different model namespace
# (e.g. claude-haiku-4.5, claude-sonnet-4.5, gpt-5) whose availability depends
# on the user's plan and isn't enumerable headlessly.
# A user-supplied --model always takes precedence.
DEFAULT_COPILOT_MODEL = "claude-sonnet-4.6"
# Dedup/consolidation is a lightweight task — pin a fast, cheap model so the
# summary pass (which runs serially after all agents) doesn't gate on a heavy
# model. Mirrors the claude path, which uses haiku for the same reason.
DEFAULT_COPILOT_SUMMARY_MODEL = "claude-haiku-4.5"


def default_model_for(provider: str) -> str | None:
    """Default main-agent model for a provider when --model is not given.

    Returns None for providers (e.g. copilot) where we defer to the CLI's own
    default rather than asserting a model we can't verify.
    """
    return DEFAULT_COPILOT_MODEL if provider == "copilot" else DEFAULT_MODEL


def default_summary_model_for(provider: str) -> str | None:
    """Default summary-agent model for a provider (None defers to the CLI)."""
    if provider == "copilot":
        return DEFAULT_COPILOT_SUMMARY_MODEL
    return DEFAULT_SUMMARY_MODEL

# --- Diff processing --------------------------------------------------------
DEFAULT_CHUNK_BUDGET = 100_000
DEFAULT_MAX_FILE_SIZE = 20_000
DEFAULT_UNIFIED_CONTEXT = 3

# --- Agents -----------------------------------------------------------------
DEFAULT_AGENTS = ["security", "quality", "performance", "testing"]
VALID_AGENTS = set(DEFAULT_AGENTS)
VALID_SEVERITIES = {"Low", "Medium", "High"}
SEVERITY_ORDER = {"High": 0, "Medium": 1, "Low": 2}

# --- Alignment review (work-item requirement coverage) ----------------------
ALIGNMENT_AGENT_NAME = "Alignment Agent"
ALIGNMENT_VERDICTS = {"Satisfied", "Partial", "Not satisfied"}
ALIGNMENT_CRITERION_STATUSES = {"Met", "Partial", "Not met", "Unverifiable"}
ALIGNMENT_CONFIDENCES = {"High", "Low"}

# --- Report output ----------------------------------------------------------
# "both" stays json+markdown for backward compatibility; "all" adds html.
VALID_FORMATS = {"json", "markdown", "html", "both", "all"}
DEFAULT_OUT_DIR = Path("./reports")
REPORT_JSON_FILENAME = "report.json"
REPORT_MARKDOWN_FILENAME = "review-report.md"
REPORT_HTML_FILENAME = "review-report.html"
ALIGNMENT_REPORT_JSON_FILENAME = "alignment-report.json"
ALIGNMENT_REPORT_HTML_FILENAME = "alignment-report.html"
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

    
    "*.png", "*.jpg", "*.jpeg", "*.gif", "*.ico", "*.webp", "*.bmp", "*.tiff",
    "*.ttf", "*.woff", "*.woff2", "*.eot", "*.otf",
    "*.mp4", "*.mp3", "*.wav", "*.avi", "*.mov", "*.webm",
    "*.pdf",
    "*.dll", "*.exe", "*.pdb", "*.nupkg",
    "*.zip", "*.tar", "*.gz", "*.rar", "*.7z",
    "*.docx", "*.xlsx", "*.pptx",
]

# --- Azure DevOps integration -----------------------------------------------
# REST API version used for all Azure DevOps calls.
AZURE_API_VERSION = "7.1"
# Work item fields fetched for the alignment review. A superset across work item
# types — different types carry the requirement in different fields (a User Story
# uses Description + AcceptanceCriteria; a Bug uses ReproSteps). We request all
# and use whichever are populated. Reading these requires the PAT to additionally
# carry the Work Items (read) scope.
AZURE_WORK_ITEM_FIELDS = (
    "System.Title",
    "System.Description",
    "Microsoft.VSTS.Common.AcceptanceCriteria",
    "Microsoft.VSTS.TCM.ReproSteps",
    "System.WorkItemType",
    "System.State",
    "System.Tags",
)
# Environment variables searched (in order) for the Personal Access Token used
# to authenticate the push. SYSTEM_ACCESSTOKEN is the token Azure Pipelines
# exposes to a job, so the same command works locally and in CI.
AZURE_PAT_ENV_VARS = ("AZURE_DEVOPS_PAT", "SYSTEM_ACCESSTOKEN")
# Local push server defaults.
PUSH_SERVER_HOST = "127.0.0.1"
# Placeholder in the rendered HTML that the push server replaces with the live
# push config (endpoint URL + one-time nonce). Left as an inert HTML comment in
# the static on-disk report, so opening that file standalone does nothing.
PUSH_CONFIG_PLACEHOLDER = "<!--PRS_PUSH_CONFIG-->"

# --- Cache ------------------------------------------------------------------
CACHE_DIR_ENV = "PR_SENTINEL_CACHE_DIR"
DEFAULT_CACHE_DIR = Path.home() / ".pr-sentinel" / "cache"
AUTO_PRUNE_AGE_DAYS = 90
AUTO_PRUNE_AGE_SECONDS = AUTO_PRUNE_AGE_DAYS * 86400

# --- CLI ---------------------------------------------------------------------
DEFAULT_BASE_BRANCH = "main"
DEFAULT_HEAD_REF = "HEAD"
DEFAULT_REMOTE = "origin"
DEFAULT_FETCH = False
DEFAULT_REPORT_FORMAT = "both"
DEFAULT_PRUNE_AGE = "30d"

RISK_STYLE = {
    "High": "red",
    "Medium": "yellow",
    "Low": "green",
    "None": "green",
    "Unknown": "red",
}
