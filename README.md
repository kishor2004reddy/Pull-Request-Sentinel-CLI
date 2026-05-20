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
[ orchestrator ] ── runs 4 agents in parallel via ThreadPoolExecutor
        │
        ├── Security Agent      (claude -p prompts/security.md)
        ├── Code Quality Agent  (claude -p prompts/quality.md)
        ├── Performance Agent   (claude -p prompts/performance.md)
        └── Testing Agent       (claude -p prompts/testing.md)
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

```bash
git clone <this repo>
cd pr-sentinel-cli
pip install -e .
```

Verify:

```bash
pr-sentinel --help
pr-sentinel agents
```

## Quickstart

Run against the bundled sample diff (works without a git repo):

```bash
pr-sentinel review --diff samples/sample.diff --out reports
```

Open `reports/review-report.md`.

## Commands

### `pr-sentinel review`

| Flag | Default | Description |
|------|---------|-------------|
| `--base` | `main` | Branch to diff against. Runs `git diff <base>...HEAD`. |
| `--diff PATH` | — | Review a saved diff file instead of running git. Mutually exclusive with `--staged`. |
| `--staged` | off | Review staged changes (`git diff --cached`). |
| `--agents` | `security,quality,performance,testing` | Comma-separated agents to run. |
| `--out` | `./reports` | Output directory. |
| `--format` | `both` | `json`, `markdown`, or `both`. |
| `--max-file-size` | `20000` | Per-file diff size cap (chars). Larger files get truncated with a marker. |
| `--chunk-budget` | `60000` | Max combined diff size per Claude call before chunking kicks in. |

### `pr-sentinel agents`

Lists available agents and their implementation status.

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

Write reports to a custom directory:
```bash
pr-sentinel review --base develop --out ./reviews/pr-42
```

## Report structure

The markdown report always emits these five sections in this fixed order, regardless of findings:

1. **Summary** — risk level, source, branch, timestamp, agents, finding counts
2. **Merge Verdict** — 3-sentence deterministic verdict (driven by risk level, not a separate Claude call)
3. **Key Findings** — top blocking issues (High + Medium)
4. **Key Recommendations** — deduplicated fixes
5. **All Findings** — full per-issue detail

The JSON report contains the same data in a single structured object suitable for piping into other tools.

### Risk levels

| Level | Trigger |
|-------|---------|
| **High** | Any High-severity finding |
| **Medium** | One or more Medium-severity findings |
| **Low** | Only Low-severity findings |
| **None** | No findings across all executed agents |

## Architecture

```
src/pr_sentinel/
├── cli.py                  # Click entrypoint
├── git_diff.py             # git rev-parse, git diff, --staged
├── diff_parser.py          # per-file splitting + noise filter + truncation
├── chunker.py              # greedy packer to keep prompts under chunk-budget
├── claude_runner.py        # subprocess(claude -p) + JSON extraction + 1 retry
├── orchestrator.py         # parallel agent execution via ThreadPoolExecutor
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

**`claude returned non-JSON output after retry`** — Claude occasionally returns prose instead of JSON. The runner retries once; if it still fails, the agent's findings for that chunk are dropped and the other agents continue. Re-running usually succeeds.

**Slow runs** — large diffs trigger chunking. Each chunk is one Claude call per agent. Reduce scope with `--agents security` if you only want one perspective, or with `--max-file-size` to truncate huge files.

**Lock files / minified files showing up** — they shouldn't. If they do, add the pattern to `NOISE_PATTERNS` in [diff_parser.py](src/pr_sentinel/diff_parser.py).

## Notes & limitations

- The Performance Agent only sees the diff. It can flag pattern-level issues (N+1 queries, sync-over-async) but cannot reason about runtime behavior or system load.
- `lineHint` is approximate — unified diffs have hunk headers, not absolute line numbers. The prompt asks Claude for a *description* of the location rather than a hallucinated number.
- The agent cannot read other files in the repo. Review depth is limited to what's visible in the diff itself.
