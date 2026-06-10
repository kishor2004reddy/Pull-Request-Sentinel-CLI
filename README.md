# PR Sentinel

[![PyPI version](https://img.shields.io/pypi/v/pr-sentinel)](https://pypi.org/project/pr-sentinel/)
[![License](https://img.shields.io/pypi/l/pr-sentinel)](https://github.com/kishor2004reddy/Pull-Request-Sentinel-CLI/blob/main/LICENSE)
[![Downloads](https://img.shields.io/pepy/dt/pr-sentinel)](https://pepy.tech/project/pr-sentinel)

Local pull-request review tool. Reads a git diff, runs four specialized review agents over it via a local AI CLI, and emits a structured JSON + Markdown report so you can fix issues *before* raising the PR.

No API keys. No hosted services. PR Sentinel shells out to a provider CLI you already have — the GitHub Copilot CLI (default) or `claude -p` (`--provider claude`) — and uses that tool's existing authentication. See [Providers](#providers).

Reports come out as JSON, Markdown, or a self-contained HTML page with editor deep-links (`--format`).

## How it works

```
git diff main...HEAD
        │
        ▼
[ diff_parser ] ── filters built-in noise + user skip patterns
        │             (--skip-files, .prsentinelignore)
        ▼
[ router ] ── per file, decides which agents are relevant for those file types
        │
        ▼
[ chunker ] ── packs each agent's files into ≤100k-char chunks (hybrid batching)
        │
        ▼
[ orchestrator ] ── runs (agent × chunk) tasks in a bounded thread pool
        │
        ├── Security Agent      (provider CLI + prompts/security.md)
        ├── Code Quality Agent  (provider CLI + prompts/quality.md)
        ├── Performance Agent   (provider CLI + prompts/performance.md)
        └── Testing Agent       (provider CLI + prompts/testing.md)
                │
                ▼
[ cache ] ── sha256(provider + model + prompt) → response, on disk
                │
                ▼
[ Summary Agent ] ── single provider call that dedupes/merges findings across agents
        │             (prompts/summary.md; falls back to raw findings on failure)
        ▼
[ report_generator ] ── merges findings, computes risk level
        │                  renders JSON / Markdown / HTML (--format)
        ▼
reports/report.json + reports/review-report.md (+ reports/review-report.html)
```

## File-type routing

Not every agent has something useful to say about every file. PR Sentinel skips agents that have nothing meaningful to contribute to a chunk's file types, reducing token usage with no loss in review quality.

| File type | Security | Quality | Performance | Testing |
|---|:---:|:---:|:---:|:---:|
| `*.cs` (source) | ✅ | ✅ | ✅ | ✅ |
| `*Test*.cs`, `*Spec.cs`, `*Fixture.cs` | ✅ | ✅ | — | ✅ |
| `*.cshtml`, `*.razor`, `*.html` | ✅ | ✅ | — | — |
| `*.js`, `*.ts` | ✅ | ✅ | ✅ | ✅ |
| `*.css`, `*.scss`, `*.less` | — | ✅ | — | — |
| `*.svg` | ✅ | — | — | — |
| `*.csproj`, `*.props`, `*.targets` | ✅ | ✅ | — | — |
| `*.sln` | — | — | — | — |
| `appsettings*.json`, `*.yml`, `*.yaml`, `*.xml`, `*.json` | ✅ | ✅ | — | — |
| `Dockerfile`, `*.bicep`, `*.tf` | ✅ | ✅ | — | — |
| `*.sql` | ✅ | ✅ | ✅ | — |
| `*.csv`, `*.tsv`, `*.resx` | ✅ | — | — | — |
| `*.md`, `*.txt`, `*.http` | ✅ | — | — | — |
| Unknown extension | ✅ | ✅ | ✅ | ✅ |

**Three rules behind the table:**
- Security runs on almost everything — secrets and PII appear in docs, config files, and data files.
- Performance and Testing only run on executable code.
- Unknown extensions always get all four agents — nothing is silently skipped.

If a chunk contains mixed file types (e.g. a `.cs` file and a `.css` file together), the agents for the union of both types are run. Binary files (`*.png`, `*.dll`, `*.zip`, etc.) are dropped entirely before routing — their diffs are unreadable.

## Requirements

- Python 3.11+
- At least one supported provider CLI, installed and authenticated:
  - **Copilot** (default) — [GitHub Copilot CLI](https://docs.github.com/en/copilot/how-tos/copilot-cli/set-up-copilot-cli/install-copilot-cli). `copilot --version` must work, and you must have run `copilot login`.
  - **Claude** (optional, `--provider claude`) — [Claude Code CLI](https://docs.claude.com/en/docs/claude-code). `claude --version` must work from your shell.
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
| `--format` | `both` | Report format(s): `json`, `markdown`, `html`, `both` (json+markdown), or `all` (json+markdown+html). |
| `--max-file-size` | `20000` | Per-file diff size cap (chars). Larger files get truncated with a marker. |
| `--chunk-budget` | `100000` | Max combined diff size per provider call before chunking kicks in. |
| `--provider` | `copilot` | AI CLI to run the agents through. `copilot` shells out to the GitHub Copilot CLI; `claude` shells out to `claude -p`. See [Providers](#providers). |
| `--model` | provider default | Model to use, forwarded verbatim to the selected provider. **claude:** shortcuts `sonnet`, `opus`, `haiku`, or a full ID like `claude-opus-4-8`, `claude-sonnet-4-6` (default `sonnet`). **copilot:** a Copilot model ID such as `claude-sonnet-4.6`, `gpt-5` (default `claude-sonnet-4.6`). |
| `--max-parallel` | `12` | Max concurrent provider calls across all (agent, chunk) pairs. |
| `--timeout` | `600` | Per-call timeout in seconds for each provider subprocess. |
| `--no-cache` | off | Bypass the response cache for this run. Successful responses are still written to the cache. |
| `--skip-files` | — | Comma-separated glob patterns to skip on top of built-in noise filters (e.g. `"*.lock,vendor/**,fixtures/*.json"`). Combines with `.prsentinelignore` if present. |

### `pr-sentinel agents`

Lists available agents and their implementation status.

### `pr-sentinel push-azure`

Push selected findings from a prior review to an Azure DevOps pull request as **PR-level comment threads** — one thread per finding. You pick *which* findings to push interactively, right in the HTML report.

```bash
# 1. Review and emit the HTML report
pr-sentinel review --base main --format all

# 2. Authenticate (PAT with Code: read & write scope)
$env:AZURE_DEVOPS_PAT = "<your-pat>"        # PowerShell
# export AZURE_DEVOPS_PAT=<your-pat>        # bash

# 3. Open the report and push selected findings to PR #124
pr-sentinel push-azure --pr 124
```

This starts a small local server on `127.0.0.1`, opens the report in your browser, and adds checkboxes plus a **"Push selected to PR"** button. Tick the findings you want, click push, and each becomes a comment thread on the PR. Press `Ctrl+C` in the terminal when done.

| Flag | Default | Description |
|------|---------|-------------|
| `--pr` | *required* | Azure DevOps pull request ID to comment on. |
| `--report PATH` | `reports/report.json` | Report produced by a prior `review` run. |
| `--org` / `--project` / `--repo` | auto-detected | Override the org/project/repo (otherwise parsed from the `origin` remote). |
| `--repo-dir PATH` | cwd | Repository whose `origin` remote is parsed for the org/project/repo. |
| `--port` | `0` | Local server port (`0` picks a free one). |
| `--no-browser` | off | Don't auto-open the browser. |

Notes:
- **The PAT never reaches the browser.** It's read from `AZURE_DEVOPS_PAT` (or `SYSTEM_ACCESSTOKEN` in Azure Pipelines) and used only by the local server, which makes the authenticated REST calls. The browser only talks to `localhost` and carries a one-time session nonce.
- **Idempotent.** Each thread is tagged with the finding's id, so re-pushing the same finding is skipped rather than duplicated.
- **Auto-detection.** The org/project/repo come from your `origin` remote (HTTPS, `visualstudio.com`, or SSH forms). Pass `--org/--project/--repo` to override or if there's no Azure DevOps remote.

### `pr-sentinel cache`

Inspect and manage the on-disk response cache.

| Subcommand | Description |
|---|---|
| `cache size` | Show cache location, entry count, and disk usage. |
| `cache clear` | Wipe the entire cache (prompts for confirmation). |
| `cache prune --older-than 30d` | Delete entries older than the given age. Supports `s/m/h/d` suffixes. Add `--dry-run` to preview. |

The cache lives at `~/.pr-sentinel/cache/` by default. Override with the `PR_SENTINEL_CACHE_DIR` environment variable. Keys are `sha256(provider + model + prompt)`, so changing the provider, the model, or any prompt content invalidates the entry automatically (and a Claude run never collides with a Copilot run that happens to use the same model name).

**Auto-pruning.** Every `pr-sentinel review` run silently drops cache entries older than 90 days before doing any work. No flag, no output — it just keeps the cache from growing unbounded over time. The threshold is set via `AUTO_PRUNE_AGE_DAYS` in [config.py](src/pr_sentinel/config.py). Use `cache prune --older-than ...` for manual prunes at a different age.

## Providers

PR Sentinel doesn't talk to any AI service directly — it shells out to a provider CLI you already have installed and authenticated. Pick one with `--provider`:

| Provider | CLI invoked | Prompt delivery | Default model |
|---|---|---|---|
| `claude` | `claude --model <m> -p` | stdin | `sonnet` |
| `copilot` (default) | `copilot --no-color [--model <m>]` | stdin | `claude-sonnet-4.6` |

Notes:

- **Models are provider-specific.** `--model` is forwarded verbatim to whichever provider you select; there's no translation between namespaces. `sonnet` means something to Claude, `gpt-5` means something to Copilot. If you pass a model the provider doesn't offer, that CLI reports the error.
- **Discovering Copilot models.** Available models depend on your GitHub Copilot plan and can't be listed non-interactively. To see what your account can use, run `copilot` and type `/model`. PR Sentinel defaults to `claude-sonnet-4.6` for Copilot; override with `--model` if your plan doesn't include it.
- **Copilot runs read-only.** PR Sentinel invokes Copilot *without* `--allow-all-tools`. The full diff is embedded in the prompt and the agent only returns a JSON verdict, so Copilot never needs to run shell commands or edit files in your repo.
- **Authentication is the provider's.** No API keys live in PR Sentinel — Claude uses your Claude Code login, Copilot uses your `copilot login` session.

```bash
# Run the review through GitHub Copilot instead of Claude
pr-sentinel review --base main --provider copilot

# Pick a specific Copilot model
pr-sentinel review --diff my.diff --provider copilot --model claude-sonnet-4.5
```

## Skipping files

PR Sentinel always filters built-in noise (lock files, build/dist dirs, minified, generated). To skip *additional* files — large fixtures, vendored code, anything that wastes tokens without adding review value — use either of these:

**Per-run flag**, good for one-offs:
```bash
pr-sentinel review --skip-files "vendor/**,fixtures/*.json,*.snap"
```

**Repo-wide ignore file**, good for project defaults — drop a `.prsentinelignore` at the repo root:
```
# generated docs
docs/api/**

# huge test fixtures
tests/fixtures/large/**

# vendored deps
vendor/**
```

Syntax: one glob per line, `#` for comments, blank lines ignored. Patterns use `fnmatch` (same as the built-in noise filter), matched against both the full path and the basename. The two sources combine — flag patterns are appended to the ignore file's patterns. Skipped files are listed in the "Skipped noise file(s)" panel at the top of the run output.

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

Also emit a browsable HTML report:
```bash
pr-sentinel review --base main --format all
```

Force a fresh run, ignoring cached responses:
```bash
pr-sentinel review --base main --no-cache
```

Skip extra files for this run only:
```bash
pr-sentinel review --base main --skip-files "vendor/**,*.snap"
```

Prune cache entries older than a week:
```bash
pr-sentinel cache prune --older-than 7d
```

## Report structure

PR Sentinel can emit three report formats (pick with `--format`), all built from the same underlying report object:

- **`report.json`** — the structured report, suitable for piping into other tools.
- **`review-report.md`** — the human-readable Markdown report (sections below).
- **`review-report.html`** — a single self-contained HTML page (inline CSS/JS, no external assets) with severity badges and `vscode://` editor deep-links straight to each finding's file.

`--format both` (the default) writes JSON + Markdown; `--format all` adds HTML. Every run also drops the raw diff it reviewed at `reports/source.diff`. At the end of each run the CLI prints a **Run Stats** panel — total time, provider calls, cache hit rate, and (when the provider reports them) tokens, cost, and Copilot premium requests.

The markdown report always emits these five sections in this fixed order, regardless of findings:

1. **Summary** — risk level, source, branch, timestamp, agents, finding counts
2. **Merge Verdict** — deterministic verdict driven by risk level (not a separate Claude call)
3. **Key Findings** — top blocking issues (High + Medium)
4. **Key Recommendations** — deduplicated fixes
5. **All Findings** — per-agent summary table + full per-issue detail

The JSON report contains the same data in a single structured object suitable for piping into other tools. Key fields:

- `riskLevel` — `High` / `Medium` / `Low` / `None` / `Unknown` (see table below)
- `coverageComplete` — `false` if any agent failed during the run
- `agentsExecuted`, `failedAgents` — who ran and who didn't
- `findings` — flat list, sorted by severity then file
- `rawFindingCount` — only present when the Summary Agent ran; how many findings existed before dedup

### Risk levels

| Level | Trigger |
|-------|---------|
| **High** | Any High-severity finding, or 5+ Medium-severity findings |
| **Medium** | One or more Medium-severity findings (and fewer than 5) |
| **Low** | Only Low-severity findings |
| **None** | No findings across all executed agents (full coverage) |
| **Unknown** | All executed agents failed, **or** some agents failed and the rest found nothing (can't call it clean with incomplete coverage) |

## Architecture

```
src/pr_sentinel/
├── cli.py                  # Click entrypoint — wires the pipeline together
├── ui.py                   # Rich panel/table builders (pure: data → renderable)
├── config.py               # tunable defaults + shared constants (single source of truth)
├── runstats.py             # thread-safe per-run metrics (calls, tokens, cost, time)
├── diff/
│   ├── git_diff.py         # git rev-parse, git diff, --staged
│   ├── diff_parser.py      # per-file splitting + noise filter (+ --skip-files / .prsentinelignore) + truncation
│   └── chunker.py          # greedy packer to keep prompts under chunk-budget
├── providers/
│   ├── __init__.py         # provider dispatch: name -> runner module (get_runner)
│   ├── common.py           # shared JSON extraction + cache + 1-retry logic + runstats
│   ├── claude.py           # subprocess(claude -p), prompt on stdin
│   └── copilot.py          # subprocess(copilot --no-color), prompt on stdin
├── orchestrator.py         # parallel (agent, chunk) execution via ThreadPoolExecutor
├── router.py               # file-type routing table — decides which agents run per chunk
├── cache.py                # sha256-keyed disk cache + 90-day auto-prune
├── push_server.py          # localhost server backing the HTML report's "Push" button
├── integrations/
│   └── azure_devops.py     # remote parsing + Azure DevOps PR comment-thread client
├── report_generator/
│   ├── __init__.py         # build_report + JSON writer + shared report-level helpers
│   ├── markdown.py         # Markdown renderer
│   └── html.py             # self-contained HTML renderer (editor deep-links)
├── agents/
│   ├── base.py             # BaseAgent: load prompt, chunk, call, validate
│   ├── security_agent.py
│   ├── quality_agent.py
│   ├── performance_agent.py
│   ├── testing_agent.py
│   └── summary_agent.py    # post-processes findings: dedupe/merge across agents
└── prompts/
    ├── security.md
    ├── quality.md
    ├── performance.md
    ├── testing.md
    └── summary.md

tests/
├── test_diff_parser.py
├── test_chunker.py
├── test_providers.py
├── test_summary_agent.py
├── test_report_generator.py
├── test_azure_devops.py
└── test_push_server.py
```

## Running tests

```bash
pip install -e .[dev]
pytest -q
```

## Troubleshooting

**`copilot CLI not found on PATH`** — install the GitHub Copilot CLI (`npm install -g @github/copilot-cli`) and confirm `copilot --version` works in the same shell where you run `pr-sentinel`.

**`claude CLI not found on PATH`** — only relevant when using `--provider claude`. Install Claude Code and confirm `claude --version` works in the same shell.

**`copilot returned non-JSON output after retry`** — the provider CLI occasionally returns prose instead of JSON. The runner retries once; if it still fails, that chunk's findings are dropped and the agent is marked failed for the run. Re-running usually succeeds.

**Slow runs** — large diffs trigger chunking. Each chunk is one provider call per agent. Reduce scope with `--agents security` if you only want one perspective, or with `--max-file-size` to truncate huge files. The cache amortizes repeat runs against the same diff.

**Lock files / minified files showing up** — they shouldn't. If they do, add the pattern to `NOISE_PATTERNS` in [config.py](src/pr_sentinel/config.py), or skip on a per-project basis with `.prsentinelignore` (see [Skipping files](#skipping-files)).

**Want to skip a non-noise file** (huge fixture, vendored dep, generated snapshot)? Use `--skip-files "pat1,pat2"` for a one-off, or commit a `.prsentinelignore` for project-wide defaults.

## Notes & limitations

- The Performance Agent only sees the diff. It can flag pattern-level issues (N+1 queries, sync-over-async) but cannot reason about runtime behavior or system load.
- `lineHint` is approximate — unified diffs have hunk headers, not absolute line numbers. The prompt asks Claude for a *description* of the location rather than a hallucinated number.
- Agents cannot read other files in the repo. Review depth is limited to what's visible in the diff itself.
- If any one chunk fails for an agent (timeout, exit code, unparseable JSON after retry), that agent is marked failed for the run and its partial findings are discarded. Other agents continue.
- The Summary Agent makes one additional provider call per run to deduplicate and merge findings across the four review agents. If it fails (timeout, bad JSON), the report falls back to the raw findings — nothing is lost.

## License

MIT
