# PR Sentinel

Local pull-request review tool. Reads a git diff, runs four specialized review agents over it via the Claude Code CLI, and emits a structured JSON + Markdown report so you can fix issues *before* raising the PR.

No API keys. No hosted services. PR Sentinel shells out to `claude -p` and uses your existing Claude Code authentication.

## How it works

```
git diff main...HEAD
        │
        ▼
[ diff_parser ] ── filters noise (lock files, build dirs, minified, generated)
        │
        ▼
[ chunker ] ── packs files into ≤60k-char chunks (hybrid batching)
        │
        ▼
[ orchestrator ] ── runs (agent × chunk) tasks in a bounded thread pool
        │
        ├── Security Agent      (claude -p prompts/security.md)
        ├── Code Quality Agent  (claude -p prompts/quality.md)
        ├── Performance Agent   (claude -p prompts/performance.md)
        └── Testing Agent       (claude -p prompts/testing.md)
                │
                ▼
[ cache ] ── sha256(model + prompt) → response, on disk
                │
                ▼
[ report_generator ] ── merges findings, computes risk level
        │
        ▼
reports/report.json + reports/review-report.md
```

## Requirements

- Python 3.11+
- [Claude Code CLI](https://docs.claude.com/en/docs/claude-code) installed and authenticated. `claude --version` must work from your shell.
- Git, if you want to review live branches (not required for `--diff` mode).

## Install

From PyPI:

```bash
pip install pr-sentinel
```

From source (for development):

```bash
git clone https://github.com/kishor2004reddy/Pull-Request-Sentinel-CLI.git
cd Pull-Request-Sentinel-CLI
pip install -e .[dev]
```

Verify:

```bash
pr-sentinel --help
pr-sentinel agents
```

## Quickstart

Review your current branch against `main`:

```bash
pr-sentinel review --base main
```

Or review a saved diff file (works without a git repo):

```bash
git diff main...HEAD > my.diff
pr-sentinel review --diff my.diff --out reports
```

Open `reports/review-report.md`.

## Commands

### `pr-sentinel review`

| Flag | Default | Description |
|------|---------|-------------|
| `--base` | `main` | Branch to diff against. Runs `git diff <base>...<head>`. |
| `--head` | `HEAD` | Source branch/ref to review. Use with `--base` to diff arbitrary refs without checking them out. |
| `--diff PATH` | — | Review a saved diff file instead of running git. Mutually exclusive with `--staged`. |
| `--staged` | off | Review staged changes (`git diff --cached`). |
| `--repo PATH` | cwd | Path to the git repository to review. Ignored when `--diff` is used. |
| `--agents` | `security,quality,performance,testing` | Comma-separated agents to run. |
| `--out` | `./reports` | Output directory. |
| `--format` | `both` | `json`, `markdown`, or `both`. |
| `--max-file-size` | `20000` | Per-file diff size cap (chars). Larger files get truncated with a marker. |
| `--chunk-budget` | `60000` | Max combined diff size per Claude call before chunking kicks in. |
| `--model` | `haiku` | Claude model to use. Shortcuts: `sonnet`, `opus`, `haiku`. Or pass a full model ID such as `claude-opus-4-7`. |
| `--max-parallel` | `8` | Max concurrent `claude` calls across all (agent, chunk) pairs. |
| `--timeout` | `600` | Per-call timeout in seconds for each `claude` subprocess. |
| `--no-cache` | off | Bypass the response cache for this run. Successful responses are still written to the cache. |

### `pr-sentinel agents`

Lists available agents and their implementation status.

### `pr-sentinel cache`

Inspect and manage the on-disk response cache.

| Subcommand | Description |
|---|---|
| `cache size` | Show cache location, entry count, and disk usage. |
| `cache clear` | Wipe the entire cache (prompts for confirmation). |
| `cache prune --older-than 30d` | Delete entries older than the given age. Supports `s/m/h/d` suffixes. Add `--dry-run` to preview. |

The cache lives at `~/.pr-sentinel/cache/` by default. Override with the `PR_SENTINEL_CACHE_DIR` environment variable. Keys are `sha256(model + prompt)`, so changing the model or any prompt content invalidates the entry automatically.

## Examples

Review current branch vs `main`:
```bash
pr-sentinel review --base main
```

Run only the security agent:
```bash
pr-sentinel review --base main --agents security
```

Review what's currently staged:
```bash
pr-sentinel review --staged
```

Review a feature branch without checking it out:
```bash
pr-sentinel review --base main --head feature/new-auth
```

Use a stronger model for higher-stakes reviews:
```bash
pr-sentinel review --base main --model opus
```

Force a fresh run, ignoring cached responses:
```bash
pr-sentinel review --base main --no-cache
```

Prune cache entries older than a week:
```bash
pr-sentinel cache prune --older-than 7d
```

## Report structure

The markdown report always emits these five sections in this fixed order, regardless of findings:

1. **Summary** — risk level, source, branch, timestamp, agents, finding counts
2. **Merge Verdict** — deterministic verdict driven by risk level (not a separate Claude call)
3. **Key Findings** — top blocking issues (High + Medium)
4. **Key Recommendations** — deduplicated fixes
5. **All Findings** — per-agent summary table + full per-issue detail

The JSON report contains the same data in a single structured object suitable for piping into other tools.

### Risk levels

| Level | Trigger |
|-------|---------|
| **High** | Any High-severity finding, or 5+ Medium-severity findings |
| **Medium** | One or more Medium-severity findings (and fewer than 5) |
| **Low** | Only Low-severity findings |
| **None** | No findings across all executed agents |
| **Unknown** | All executed agents failed — risk could not be determined |

## Architecture

```
src/pr_sentinel/
├── cli.py                  # Click entrypoint + Rich UI
├── git_diff.py             # git rev-parse, git diff, --staged
├── diff_parser.py          # per-file splitting + noise filter + truncation
├── chunker.py              # greedy packer to keep prompts under chunk-budget
├── claude_runner.py        # subprocess(claude -p) + JSON extraction + 1 retry
├── orchestrator.py         # parallel (agent, chunk) execution via ThreadPoolExecutor
├── cache.py                # sha256-keyed disk cache for Claude responses
├── report_generator.py     # build_report + JSON/Markdown writers
├── agents/
│   ├── base.py             # BaseAgent: load prompt, chunk, call, validate
│   ├── security_agent.py
│   ├── quality_agent.py
│   ├── performance_agent.py
│   └── testing_agent.py
└── prompts/
    ├── security.md
    ├── quality.md
    ├── performance.md
    └── testing.md

tests/
├── test_diff_parser.py
├── test_chunker.py
└── test_report_generator.py
```

## Running tests

```bash
pip install -e .[dev]
pytest -q
```

## Troubleshooting

**`claude CLI not found on PATH`** — install Claude Code and confirm `claude --version` works in the same shell where you run `pr-sentinel`.

**`claude returned non-JSON output after retry`** — Claude occasionally returns prose instead of JSON. The runner retries once; if it still fails, that chunk's findings are dropped and the agent is marked failed for the run. Re-running usually succeeds.

**Slow runs** — large diffs trigger chunking. Each chunk is one Claude call per agent. Reduce scope with `--agents security` if you only want one perspective, or with `--max-file-size` to truncate huge files. The cache amortizes repeat runs against the same diff.

**Lock files / minified files showing up** — they shouldn't. If they do, add the pattern to `NOISE_PATTERNS` in [diff_parser.py](src/pr_sentinel/diff_parser.py).

## Notes & limitations

- The Performance Agent only sees the diff. It can flag pattern-level issues (N+1 queries, sync-over-async) but cannot reason about runtime behavior or system load.
- `lineHint` is approximate — unified diffs have hunk headers, not absolute line numbers. The prompt asks Claude for a *description* of the location rather than a hallucinated number.
- Agents cannot read other files in the repo. Review depth is limited to what's visible in the diff itself.
- If any one chunk fails for an agent (timeout, exit code, unparseable JSON after retry), that agent is marked failed for the run and its partial findings are discarded. Other agents continue.

## License

MIT
