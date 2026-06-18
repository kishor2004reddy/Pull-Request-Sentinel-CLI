# PR Sentinel

[![PyPI version](https://img.shields.io/pypi/v/pr-sentinel)](https://pypi.org/project/pr-sentinel/)
[![License](https://img.shields.io/pypi/l/pr-sentinel)](https://github.com/kishor2004reddy/Pull-Request-Sentinel-CLI/blob/main/LICENSE)
[![Downloads](https://img.shields.io/pepy/dt/pr-sentinel)](https://pepy.tech/project/pr-sentinel)

Local pull-request review tool. Reads a git diff, runs four specialized review agents over it via a local AI CLI, and emits a structured JSON + Markdown report so you can fix issues *before* raising the PR.

With `--pr`, the review diffs the PR's own branches, checks whether the change satisfies the PR's linked Azure DevOps work item(s) (`--align`), and ends in an interactive HTML page where you can select findings, gaps, and alignment verdicts to push back to the PR as comment threads — all in one command.

No API keys. No hosted services. PR Sentinel shells out to a provider CLI you already have — the Claude Code CLI (default) or the GitHub Copilot CLI (`--provider copilot`) — and uses that tool's existing authentication. See [Providers](#providers).

Reports come out as JSON, Markdown, or a self-contained HTML page with editor deep-links (`--format`).

## How it works

```
git diff main...HEAD  (or PR branches when --pr is given)
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
        │
        ▼    ← with --align (or `review-alignment`)
[ Alignment Agent ] ── one provider call per linked work item, whole diff in one prompt
        │               judges whether each acceptance criterion is satisfied
        │               (prompts/alignment.md; diff capped at 500k chars)
        ▼
[ report_generator ] ── merges code findings + gap findings + alignment verdicts
        │                  renders JSON / Markdown / HTML (--format)
        ▼
reports/report.json + reports/review-report.md (+ review-report.html)
        │
        ▼    ← with --pr (or `push-azure`)
[ push_server ] ── local server backing the interactive HTML report
                   tick findings / gaps / verdicts → push to Azure DevOps PR
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
  - **Claude** (default) — [Claude Code CLI](https://docs.claude.com/en/docs/claude-code). `claude --version` must work from your shell.
  - **Copilot** (optional, `--provider copilot`) — [GitHub Copilot CLI](https://docs.github.com/en/copilot/how-tos/copilot-cli/set-up-copilot-cli/install-copilot-cli). `copilot --version` must work, and you must have run `copilot login`.
- Git, if you want to review live branches (not required for `--diff` mode).
- An Azure DevOps Personal Access Token when using `--pr`, `--align`, `push-azure`, or `review-alignment`. See [Azure DevOps PAT](#azure-devops-pat).

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

Open `reports/review-report.html`.

Review a PR, push findings to it, and also check requirement coverage:

```bash
# Diffs the PR's own branches, asks for a PAT interactively, runs the code
# review + alignment agent, then opens a push page in your browser.
pr-sentinel review --pr 124 --align
```

## Commands

### `pr-sentinel review`

Review a diff and write a structured report. With `--pr`, the command also opens an interactive push page.

| Flag | Default | Description |
|------|---------|-------------|
| `--base` | `main` | Branch to diff against. Runs `git diff <base>...<head>`. |
| `--head` | `HEAD` | Source branch/ref to review. Use with `--base` to diff arbitrary refs without checking them out. |
| `--diff PATH` | — | Review a saved diff file instead of running git. Mutually exclusive with `--staged`. |
| `--staged` | off | Review staged changes (`git diff --cached`). |
| `--fetch` | off | Fetch both sides of the diff from the remote before diffing. Rewrites `base` → `{remote}/{base}` and `head` → `{remote}/{current_branch}` so the review matches exactly what Azure DevOps shows. Enabled automatically when `--pr` derives branches. |
| `--remote` | `origin` | Git remote to fetch from and to use for Azure DevOps remote detection. Use when your Azure remote is not named `origin`. |
| `--repo PATH` | cwd | Path to the git repository to review. Ignored when `--diff` is used. |
| `--agents` | `security,quality,performance,testing` | Comma-separated agents to run. |
| `--out` | `./reports` | Output directory. |
| `--format` | `both` | Report format(s): `json`, `markdown`, `html`, `both` (json+html, default), or `all` (json+html+markdown). |
| `--max-file-size` | `20000` | Per-file diff size cap (chars). Larger files get truncated with a marker. |
| `--chunk-budget` | `100000` | Max combined diff size per provider call before chunking kicks in. Does not affect the alignment agent (see `ALIGNMENT_DIFF_BUDGET`). |
| `--provider` | `claude` | AI CLI to run the agents through. `claude` shells out to `claude -p`; `copilot` shells out to the GitHub Copilot CLI. See [Providers](#providers). |
| `--model` | provider default | Model to use, forwarded verbatim to the selected provider. **claude:** shortcuts `sonnet`, `opus`, `haiku`, or a full ID like `claude-opus-4-8`, `claude-sonnet-4-6` (default `sonnet`). **copilot:** a Copilot model ID such as `claude-sonnet-4.6`, `gpt-5` (default `claude-sonnet-4.6`). |
| `--max-parallel` | `12` | Max concurrent provider calls across all (agent, chunk) pairs. |
| `--timeout` | `600` | Per-call timeout in seconds for each provider subprocess. |
| `--no-cache` | off | Bypass the response cache for this run. Successful responses are still written to the cache. |
| `--skip-files` | — | Comma-separated glob patterns to skip on top of built-in noise filters. Combines with `.prsentinelignore` if present. |
| `--pr ID` | — | Azure DevOps pull request ID. Auto-derives base/head branches from the PR, fetches them, runs the review against the PR's own diff, and ends with an interactive push page. Requires a PAT (prompted interactively if not set in the environment). |
| `--align` | off | After the code review, run the Alignment Agent to check whether the change satisfies the PR's linked work item(s). Requires `--pr`. The combined report shows code findings, alignment verdicts, and gaps all in one page. |
| `--org` | auto | Azure DevOps organization (overrides remote detection; used with `--pr`). |
| `--project` | auto | Azure DevOps project (overrides remote detection; used with `--pr`). |
| `--azure-repo` | auto | Azure DevOps repository name (overrides remote detection; used with `--pr`). Distinct from `--repo`, which is the local path. |
| `--port` | `0` | Local push-server port (with `--pr`). `0` picks a free port. |
| `--no-browser` | off | Don't auto-open the combined report in a browser (with `--pr`). |

### `pr-sentinel review-alignment`

Standalone requirement-coverage review. Fetches the work item(s) linked to a PR (or a specific work item), diffs the branches, and runs the Alignment Agent to judge whether each acceptance criterion is satisfied.

```bash
# Review work items linked to PR #124 against the current branch
pr-sentinel review-alignment --pr 124

# Review a specific work item against an explicit diff range
pr-sentinel review-alignment --work-item 456 --base main --head feature/csv-export

# Use with a non-default remote
pr-sentinel review-alignment --pr 124 --remote azure
```

| Flag | Default | Description |
|------|---------|-------------|
| `--pr ID` | — | Azure DevOps PR whose linked work items are reviewed. |
| `--work-item ID` | — | Review a specific work item directly (without a PR). Mutually exclusive with `--pr`. |
| `--base` | `main` | Base branch to diff against. |
| `--head` | `HEAD` | Source branch/ref. |
| `--fetch` | off | Fetch branches from the remote before diffing. Enabled automatically when `--pr` derives branches. |
| `--remote` | `origin` | Git remote for Azure detection and `--fetch`. |
| `--repo-dir PATH` | cwd | Repository to diff and whose remote is used for detection. |
| `--out` | `./reports` | Output directory for the alignment report. |
| `--provider` | `claude` | AI CLI. |
| `--model` | provider default | Model forwarded to the provider. |
| `--timeout` | `600` | Per-call timeout in seconds. |
| `--no-cache` | off | Bypass the response cache. |
| `--org` / `--project` / `--repo` | auto | Override org/project/repo (otherwise parsed from the `origin` remote). |

Writes `reports/alignment-report.json` and `reports/alignment-report.html`. To post the gaps and verdicts to a PR:

```bash
pr-sentinel push-azure --pr 124 --report reports/alignment-report.json
```

### `pr-sentinel agents`

Lists available agents and their implementation status.

### `pr-sentinel push-azure`

Push selected findings, gaps, and/or alignment verdicts from a prior review to an Azure DevOps pull request as PR-level comment threads. You pick which items to push interactively in the HTML report.

```bash
# 1. Review (HTML + JSON are written by default)
pr-sentinel review --base main

# 2. Push selected findings to PR #124 (PAT is prompted interactively)
pr-sentinel push-azure --pr 124
```

Or push the output of a `review-alignment` run:

```bash
pr-sentinel push-azure --pr 124 --report reports/alignment-report.json
```

This starts a small local server on `127.0.0.1`, opens the report in your browser, and adds checkboxes plus a **"Push selected to PR"** button. Tick the items you want, click push, and each becomes a comment thread on the PR. Press `Ctrl+C` in the terminal when done.

| Flag | Default | Description |
|------|---------|-------------|
| `--pr` | *required* | Azure DevOps pull request ID to comment on. |
| `--report PATH` | `reports/report.json` | Report produced by a prior `review` or `review-alignment` run. |
| `--org` / `--project` / `--repo` | auto-detected | Override the org/project/repo (otherwise parsed from the `origin` remote). |
| `--remote` | `origin` | Git remote whose URL is parsed for org/project/repo detection. |
| `--repo-dir PATH` | cwd | Repository whose `origin` remote is parsed for the org/project/repo. |
| `--port` | `0` | Local server port (`0` picks a free one). |
| `--no-browser` | off | Don't auto-open the browser. |

Notes:
- **The PAT never reaches the browser.** It is read from `AZURE_DEVOPS_PAT` (or `SYSTEM_ACCESSTOKEN` in Azure Pipelines), or prompted interactively if neither is set. It is used only by the local server for authenticated REST calls, never stored to disk, and never pushed into `os.environ` (which would leak it into provider subprocesses).
- **Idempotent for findings.** Each finding thread is tagged with the finding's stable id, so re-pushing the same finding is skipped rather than duplicated.
- **Upsert for alignment verdicts.** Alignment verdict threads are tagged separately. Re-pushing an already-posted verdict patches the existing comment, so a second push always refreshes it on the PR.
- **Already-pushed detection.** On page load, the server queries Azure DevOps live and marks items that are already on the PR. Soft-deleted comment threads (deleted in Azure but not yet re-pushed) are excluded from the "already pushed" count.
- **Auto-detection.** The org/project/repo come from your `origin` remote (HTTPS, `visualstudio.com`, or SSH forms). Pass `--org/--project/--repo` to override or if there's no Azure DevOps remote.

### `pr-sentinel config`

Set personal defaults for review flags so you don't have to repeat them on every run. Settings are stored in `~/.pr-sentinel/config.toml` and apply to the `review` command. Explicit CLI flags always take precedence over config defaults.

```bash
# Set a default provider
pr-sentinel config set provider copilot

# Run only security and quality agents by default
pr-sentinel config set agents security,quality

# Change the default base branch
pr-sentinel config set base develop

# Reduce parallelism (useful if you hit rate limits)
pr-sentinel config set max-parallel 6

# See all keys, your overrides, and built-in defaults
pr-sentinel config list

# Revert a single key to its built-in default
pr-sentinel config unset provider

# Clear every override at once
pr-sentinel config reset
```

| Subcommand | Description |
|---|---|
| `config set <key> <value>` | Set a default. Key names match flag names without leading dashes (`max-parallel` or `max_parallel` both work). Value is validated immediately. |
| `config unset <key>` | Remove a single default, reverting that key to its built-in value. |
| `config list` | Show all configurable keys, your current overrides, and the built-in defaults. |
| `config reset` | Remove all overrides (prompts for confirmation). Deletes the config file. |

**Configurable keys:**

| Key | Built-in default | Accepts |
|-----|-----------------|---------|
| `provider` | `copilot` | `claude`, `copilot` |
| `model` | provider default | any model string |
| `agents` | `security,quality,performance,testing` | comma-separated subset |
| `base` | `main` | any branch/ref |
| `remote` | `origin` | any remote name |
| `fetch` | `false` | `true`, `false` |
| `format` | `both` | `json`, `markdown`, `html`, `both`, `all` |
| `out` | `./reports` | any directory path |
| `max_parallel` | `12` | positive integer |
| `timeout` | `600` | positive integer (seconds) |
| `max_file_size` | `20000` | positive integer (chars) |
| `chunk_budget` | `100000` | positive integer (chars) |

**Precedence:** `built-in defaults` → `~/.pr-sentinel/config.toml` → `CLI flags`

### `pr-sentinel cache`

Inspect and manage the on-disk response cache.

| Subcommand | Description |
|---|---|
| `cache size` | Show cache location, entry count, and disk usage. |
| `cache clear` | Wipe the entire cache (prompts for confirmation). |
| `cache prune --older-than 30d` | Delete entries older than the given age. Supports `s/m/h/d` suffixes. Add `--dry-run` to preview. |

The cache lives at `~/.pr-sentinel/cache/` by default. Override with the `PR_SENTINEL_CACHE_DIR` environment variable. Keys are `sha256(provider + model + prompt)`, so changing the provider, the model, or any prompt content invalidates the entry automatically (and a Claude run never collides with a Copilot run that happens to use the same model name).

**Auto-pruning.** Every `pr-sentinel review` run silently drops cache entries older than 90 days before doing any work. No flag, no output — it just keeps the cache from growing unbounded over time. The threshold is set via `AUTO_PRUNE_AGE_DAYS` in [config.py](src/pr_sentinel/config.py). Use `cache prune --older-than ...` for manual prunes at a different age.

## Azure DevOps PAT

Commands that talk to Azure DevOps (`review --pr`, `review --align`, `review-alignment`, `push-azure`) need a Personal Access Token.

**Interactive prompt (recommended for local use).** If neither `AZURE_DEVOPS_PAT` nor `SYSTEM_ACCESSTOKEN` is set and stdin is a terminal, PR Sentinel prompts for the PAT with hidden input. The value lives only in the current process's memory for the run — it is never stored to disk and is deliberately not pushed into `os.environ` (which would leak it into the provider CLI subprocesses that are spawned during the review).

**Environment variable (recommended for CI).** Set `AZURE_DEVOPS_PAT` before running. If you're inside an Azure Pipelines job, `SYSTEM_ACCESSTOKEN` is also recognized.

```bash
# PowerShell
$env:AZURE_DEVOPS_PAT = "<your-pat>"

# bash
export AZURE_DEVOPS_PAT=<your-pat>
```

**Required scopes by command:**

| Command | Required PAT scopes |
|---------|---------------------|
| `review --pr` | Code: read & write |
| `review --pr --align` | Code: read & write, Work Items: read |
| `review-alignment` | Work Items: read, Code: read |
| `push-azure` | Code: read & write |

## Alignment Agent

The Alignment Agent is a separate, holistic review pass that checks whether the PR's code changes actually satisfy each work item's acceptance criteria. Unlike the four code-review agents (which chunk large diffs), the Alignment Agent receives the **entire diff in a single provider call** per work item, so it can reason about cross-file interactions that would be lost in chunks.

**How it works:**
1. Fetches the work item(s) linked to the PR from Azure DevOps.
2. Formats the work item — title, description, acceptance criteria, repro steps (for bugs).
3. Sends one prompt per work item: the formatted work item + the complete diff.
4. Returns a verdict (`Satisfied` / `Partial` / `Not satisfied`), a confidence rating, a summary, per-criterion trace (`Met` / `Partial` / `Not met` / `Unverifiable`), and a list of gap findings (pushable as PR comments).

**Diff budget.** The alignment agent uses `ALIGNMENT_DIFF_BUDGET = 500,000 chars` (about 150k–165k tokens), separate from the routed agents' `DEFAULT_CHUNK_BUDGET = 100,000 chars`. This covers virtually every real PR in one call while staying well clear of the model's context window. Diffs above the budget are truncated with a visible warning.

**Triggering alignment:**
- `review --pr <id> --align` — runs alignment after the code review; shows everything in one combined HTML report.
- `review-alignment --pr <id>` — standalone alignment pass; writes `alignment-report.json` and `alignment-report.html`.

## Providers

PR Sentinel doesn't talk to any AI service directly — it shells out to a provider CLI you already have installed and authenticated. Pick one with `--provider`:

| Provider | CLI invoked | Prompt delivery | Default model |
|---|---|---|---|
| `claude` (default) | `claude --model <m> -p` | stdin | `sonnet` |
| `copilot` | `copilot --no-color [--model <m>]` | stdin | `claude-sonnet-4.6` |

Notes:

- **Models are provider-specific.** `--model` is forwarded verbatim to whichever provider you select; there's no translation between namespaces. `sonnet` means something to Claude, `gpt-5` means something to Copilot. If you pass a model the provider doesn't offer, that CLI reports the error.
- **Discovering Copilot models.** Available models depend on your GitHub Copilot plan and can't be listed non-interactively. To see what your account can use, run `copilot` and type `/model`. PR Sentinel defaults to `claude-sonnet-4.6` for Copilot; override with `--model` if your plan doesn't include it.
- **Copilot runs read-only.** PR Sentinel invokes Copilot *without* `--allow-all-tools`. The full diff is embedded in the prompt and the agent only returns a JSON verdict, so Copilot never needs to run shell commands or edit files in your repo.
- **Authentication is the provider's.** No API keys live in PR Sentinel — Claude uses your Claude Code login, Copilot uses your `copilot login` session.

```bash
# Run the review through Claude (default)
pr-sentinel review --base main

# Run the review through GitHub Copilot instead
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

Also emit a Markdown report (HTML + JSON are the default):
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

**Review a PR and push findings — interactive, one command:**
```bash
# Auto-derives branches, prompts for PAT, opens push page when done.
pr-sentinel review --pr 124
```

**Review + alignment in one pass — push findings, gaps, and verdicts:**
```bash
pr-sentinel review --pr 124 --align
```

**Standalone alignment review for a PR:**
```bash
pr-sentinel review-alignment --pr 124
# Then push the alignment output:
pr-sentinel push-azure --pr 124 --report reports/alignment-report.json
```

**Alignment for a specific work item (no PR):**
```bash
pr-sentinel review-alignment --work-item 456 --base main --head feature/csv-export
```

**Alignment via non-default remote:**
```bash
pr-sentinel review --pr 124 --align --remote azure
```

Prune cache entries older than a week:
```bash
pr-sentinel cache prune --older-than 7d
```

Set personal defaults so you never have to repeat them:
```bash
pr-sentinel config set provider copilot
pr-sentinel config set agents security,quality
pr-sentinel config set base develop
pr-sentinel config list
```

## Report structure

PR Sentinel can emit several report formats (pick with `--format`), all built from the same underlying report object:

- **`report.json`** — the structured report, suitable for piping into other tools.
- **`review-report.md`** — the human-readable Markdown report.
- **`review-report.html`** — a single self-contained HTML page (inline CSS/JS, no external assets) with severity badges and `vscode://` editor deep-links straight to each finding's file.
- **`alignment-report.json`** / **`alignment-report.html`** — written by `review-alignment`; contain the alignment verdict sections and gap findings.

`--format both` (the default) writes JSON + HTML; `--format all` adds Markdown. Every run also drops the raw diff it reviewed at `reports/source.diff`. At the end of each run the CLI prints a **Run Stats** panel — total time, provider calls, cache hit rate, and (when the provider reports them) tokens, cost, and Copilot premium requests.

The `review` command automatically selects the HTML renderer based on report content: combined when both code findings and alignment are present, alignment-only when only alignment ran, or a plain code review page otherwise.

### Code review HTML report

A self-contained HTML page with:
- **Overview tab** — risk level, merge verdict, PR section (when `--pr` was used: PR number linked to Azure DevOps, repo, base branch, source branch), agent summary table.
- **Code Review tab** — all findings with severity badges, file paths, line hints, reasoning, and `vscode://` deep-links.
- **Interactive push toolbar** — checkboxes per finding/gap/verdict, a **"Push selected to PR"** button, and real-time "already pushed" detection on page load.

### Combined HTML report (`review --pr --align`)

When both code review findings and alignment verdicts are present, a single combined page is rendered:
- **Overview tab** — PR info, risk level, agent summary.
- **Code Review tab** — code findings from the four routed agents.
- **Alignment tab** — per-work-item verdict badge, acceptance-criterion traceability matrix, coverage bar, and gap findings.
- **Gaps tab** — gap findings surfaced by the Alignment Agent, pushable as PR threads.
- One push toolbar that handles all three item types: code findings, gap findings, and alignment verdicts.

### Alignment HTML report (`review-alignment`)

Stand-alone alignment report with:
- Per-work-item verdict badge (`Satisfied` / `Partial` / `Not satisfied`) and confidence.
- Acceptance-criterion traceability matrix with `Met` / `Partial` / `Not met` / `Unverifiable` status and evidence snippets.
- Coverage bar.
- Gap findings section.
- Push toolbar (gaps and verdicts are pushed via `push-azure --report reports/alignment-report.json`).

### Markdown report sections

The markdown report always emits these five sections in this fixed order, regardless of findings:

1. **Summary** — risk level, source, branch, timestamp, agents, finding counts
2. **Merge Verdict** — deterministic verdict driven by risk level (not a separate Claude call)
3. **Key Findings** — top blocking issues (High + Medium)
4. **Key Recommendations** — deduplicated fixes
5. **All Findings** — per-agent summary table + full per-issue detail

### JSON report

The JSON report contains the same data in a single structured object suitable for piping into other tools. Key fields:

- `riskLevel` — `High` / `Medium` / `Low` / `None` / `Unknown` (see table below)
- `coverageComplete` — `false` if any agent failed during the run
- `agentsExecuted`, `failedAgents` — who ran and who didn't
- `findings` — flat list, sorted by severity then file; each finding has a stable `id` for push tracking
- `rawFindingCount` — only present when the Summary Agent ran; how many findings existed before dedup
- `alignment` — list of per-work-item verdict sections (present when `--align` was used or `review-alignment` was run)
- `pr` — PR context object (present when `--pr` was used): `id`, `org`, `project`, `repo`, `title`, `baseBranch`, `sourceBranch`

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
│                             (review, review-alignment, push-azure, agents, cache, config)
├── ui.py                   # Rich panel/table builders (pure: data → renderable)
├── config.py               # tunable defaults + shared constants (single source of truth)
│                             ALIGNMENT_DIFF_BUDGET (500k chars, separate from chunk budget)
│                             load_user_config / save_user_config → ~/.pr-sentinel/config.toml
├── runstats.py             # thread-safe per-run metrics (calls, tokens, cost, time)
├── diff/
│   ├── git_diff.py         # git rev-parse, git diff, --staged, fetch, get_current_branch
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
├── push_server.py          # local server backing the HTML report's "Push" button
│                             /pushed endpoint: live-queries Azure for already-pushed finding ids
│                             and alignment verdict ids (handles Azure soft-delete)
├── integrations/
│   └── azure_devops.py     # remote parsing + Azure DevOps PR client
│                             list_thread_finding_ids, list_alignment_work_item_ids,
│                             upsert_alignment_comment, get_pr_work_items, get_work_items
├── report_generator/
│   ├── __init__.py         # build_report + JSON/Markdown/HTML writers + shared helpers
│   ├── markdown.py         # Markdown renderer
│   └── html.py             # self-contained HTML renderer (tabbed, editor deep-links)
│                             _render_html (code review), _render_alignment_html,
│                             _render_combined_html (review + alignment in one page)
├── agents/
│   ├── base.py             # BaseAgent: load prompt, chunk, call, validate
│   ├── security_agent.py
│   ├── quality_agent.py
│   ├── performance_agent.py
│   ├── testing_agent.py
│   ├── summary_agent.py    # post-processes findings: dedupe/merge across agents
│   └── alignment_agent.py  # holistic per-work-item verdict (whole diff in one call)
└── prompts/
    ├── security.md
    ├── quality.md
    ├── performance.md
    ├── testing.md
    ├── summary.md
    └── alignment.md

tests/
├── test_diff_parser.py
├── test_chunker.py
├── test_providers.py
├── test_summary_agent.py
├── test_report_generator.py
├── test_azure_devops.py
├── test_push_server.py
└── test_cli.py
```

## Running tests

```bash
pip install -e .[dev]
pytest -q
```

## Troubleshooting

**`claude CLI not found on PATH`** — Claude Code is the default provider. Install Claude Code and confirm `claude --version` works in the same shell where you run `pr-sentinel`.

**`copilot CLI not found on PATH`** — only relevant when using `--provider copilot`. Install the GitHub Copilot CLI (`npm install -g @github/copilot-cli`) and confirm `copilot --version` works in the same shell.

**`copilot returned non-JSON output after retry`** — the provider CLI occasionally returns prose instead of JSON. The runner retries once; if it still fails, that chunk's findings are dropped and the agent is marked failed for the run. Re-running usually succeeds.

**Slow runs** — large diffs trigger chunking. Each chunk is one provider call per agent. Reduce scope with `--agents security` if you only want one perspective, or with `--max-file-size` to truncate huge files. The cache amortizes repeat runs against the same diff.

**Lock files / minified files showing up** — they shouldn't. If they do, add the pattern to `NOISE_PATTERNS` in [config.py](src/pr_sentinel/config.py), or skip on a per-project basis with `.prsentinelignore` (see [Skipping files](#skipping-files)).

**Want to skip a non-noise file** (huge fixture, vendored dep, generated snapshot)? Use `--skip-files "pat1,pat2"` for a one-off, or commit a `.prsentinelignore` for project-wide defaults.

**`No Azure DevOps PAT found`** — run in a terminal (not piped) to get the interactive PAT prompt, or set `AZURE_DEVOPS_PAT` in your environment before running. In Azure Pipelines, set `SYSTEM_ACCESSTOKEN`. See [Azure DevOps PAT](#azure-devops-pat) for required scopes.

**`No linked work items found for PR #…`** — the PR must have Azure DevOps work items linked to it (via the "Related work items" section in the PR). If none are linked, `--align` will still run the code review but skip the alignment pass.

**Alignment verdict shows "pushed" but comment is not visible in Azure** — this happens when a PR comment thread was soft-deleted in Azure (Azure keeps the thread with the comment marked as deleted). PR Sentinel detects this and re-posts the verdict as a fresh thread when you push again. If a verdict still isn't appearing, try re-pushing via the HTML report.

**Gap id changed between runs (item shows as pushable again after being pushed)** — gap finding ids are derived from the finding's content (agent + file + issue text). If the model rephrases the issue slightly between runs, the id changes. This is expected behavior; push the new gap if it still represents a real issue.

## Notes & limitations

- The Performance Agent only sees the diff. It can flag pattern-level issues (N+1 queries, sync-over-async) but cannot reason about runtime behavior or system load.
- `lineHint` is approximate — unified diffs have hunk headers, not absolute line numbers. The prompt asks the model for a *description* of the location rather than a hallucinated number.
- Agents cannot read other files in the repo. Review depth is limited to what's visible in the diff itself.
- If any one chunk fails for an agent (timeout, exit code, unparseable JSON after retry), that agent is marked failed for the run and its partial findings are discarded. Other agents continue.
- The Summary Agent makes one additional provider call per run to deduplicate and merge findings across the four review agents. If it fails (timeout, bad JSON), the report falls back to the raw findings — nothing is lost.
- The Alignment Agent makes one provider call per linked work item, each carrying the whole diff (up to 500k chars). For PRs with many linked work items, this runs sequentially. Very large diffs are truncated at 500k chars with a visible warning; the verdict is then partial.

## License

MIT
