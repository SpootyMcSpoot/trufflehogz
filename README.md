# TruffleHog Organization Scanner

> **Automated secret scanning for GitHub organizations — 40-80% faster with intelligent sharding.**

Production-ready GitHub Actions workflows that scan multiple GitHub organizations with [TruffleHog OSS](https://github.com/trufflesecurity/trufflehog), create issues for findings, and generate actionable summaries.

---

## Table of Contents

- [Quick Start](#quick-start)
- [How It Works](#how-it-works)
- [Repository Structure](#repository-structure)
- [Workflows](#workflows)
- [Scanner Features](#scanner-features)
- [Issue Creation](#issue-creation)
- [Issue Feedback Loop](#issue-feedback-loop)
- [Workflow Summary](#workflow-summary)
- [Configuration Reference](#configuration-reference)
- [Running Locally](#running-locally)
- [Testing](#testing)
- [Remediating Discovered Secrets](#remediating-discovered-secrets)
- [Troubleshooting](#troubleshooting)
- [NIST 800-53 Rev 5 Compliance Coverage](#nist-800-53-rev-5-compliance-coverage)
- [Documentation Index](#documentation-index)

---

## Quick Start

### 1. Add Authentication

Go to **Settings → Secrets and variables → Actions → Secrets** and add one of:

| Method | Secrets to Add | Guide |
|--------|---------------|-------|
| **GitHub App** (recommended) | `GH_APP_ID`, `GH_APP_PRIVATE_KEY`, `GH_APP_INSTALLATION_ID` | [GITHUB_APP_SETUP.md](GITHUB_APP_SETUP.md) |
| **PAT** (fallback) | `GH_PAT` with `repo` + `read:org` scopes | [SETUP.md](SETUP.md) |

### 2. Set Target Organizations

Go to **Settings → Secrets and variables → Actions → Variables** and add:

```
TRUFFLEHOG_ORGS = org1,org2,org3
```

### 3. Run the Scan

**Actions → TruffleHog Org Scan → Run workflow**

The workflow runs automatically every **Sunday at 10 AM PST** and can be triggered manually at any time.

### 4. Review Results

- **Workflow summary** — metrics, severity breakdown, per-repo findings (visible on the Actions run page)
- **GitHub issues** — created automatically when `open_issues: true` is enabled
- **Artifacts** — raw NDJSON findings downloadable from each run

---

## How It Works

```mermaid
flowchart TD
    Start([Workflow Triggered]) --> Plan[1. plan<br/>Count repos per org<br/>Calculate optimal shards]
    Plan --> S1[2a. scan shard 1]
    Plan --> S2[2b. scan shard 2]
    Plan --> SN[2c. scan shard N]
    
    subgraph Feedback["Feedback Loop (Active)"]
        Labels[Issue Labels]
        Comments[Comment Commands]
        IFW[issue-feedback.yml]
        Comments --> IFW --> Labels
    end

    S1 --> Summarize[3. summarize-org<br/>Merge NDJSON · Filter FPs<br/>Check Suppressed Hashes<br/>Generate summary · Create issues]
    S2 --> Summarize
    SN --> Summarize
    Labels -.->|Suppression Check| Summarize
    
    Summarize --> WS[4. workflow-summary<br/>Config table · Metrics · Links]
    WS --> End([Complete])

    style Start fill:#2e7d32,stroke:#1b5e20,color:#fff
    style End fill:#2e7d32,stroke:#1b5e20,color:#fff
    style Plan fill:#1976d2,stroke:#0d47a1,color:#fff
    style S1 fill:#7b1fa2,stroke:#4a148c,color:#fff
    style S2 fill:#7b1fa2,stroke:#4a148c,color:#fff
    style SN fill:#7b1fa2,stroke:#4a148c,color:#fff
    style Summarize fill:#c2185b,stroke:#880e4f,color:#fff
    style WS fill:#0097a7,stroke:#006064,color:#fff
    style Feedback fill:#f5f5f5,stroke:#9e9e9e,stroke-dasharray: 5 5
```

**Sharding**: Repos are distributed across parallel shards (default: modulo distribution). Each shard scans its repos concurrently, then uploads NDJSON artifacts. The summarize job merges results, deduplicates, and optionally creates issues.

### Performance

| Org Size | Standard Scan | With Sharding | Improvement |
|----------|--------------|---------------|-------------|
| 25 repos | 3-5 min | 1-2 min | ~60% |
| 150 repos | 8-12 min | 2-3 min | ~75% |
| 500 repos | 15-25 min | 3-5 min | ~80% |
| 1500 repos | 30-60 min | 5-10 min | ~83% |

---

## Repository Structure

```
.github/
  dependabot.yml              # Automated dependency updates (pip + github-actions)
  trufflehog/
    false_positives.txt        # Regex patterns for false-positive suppression
  workflows/
    trufflehog-org-scan.yml    # Main org-wide scan workflow
    self-scan.yml              # Self-test scan against this repo
    pr-validation.yml          # 6-stage PR gate
    lint-workflows.yml         # actionlint + ghalint
    security-baseline-check.yml # Permissions/SHA/timeouts audit
scripts/
  deploy.py                   # Deployment helper (test fixture)
test/
  fixtures/                   # Intentional test secrets for validation
  test-config.json            # Test configuration with sample credentials
trufflehog_scanner.py         # Main scanner script (~1550 lines)
test_trufflehog_scanner.py    # Unit tests (122 tests)
requirements.txt              # Python dependencies (PyJWT, cryptography, pytest)
```

---

## Workflows

All actions are **SHA-pinned** with `persist-credentials: false` on every checkout step.

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| **trufflehog-org-scan** | Schedule (Sun 10 AM PST) + manual | Main org-wide secret scanning |
| **self-scan** | Every push + manual | End-to-end validation against this repo's test fixtures |
| **pr-validation** | Pull requests + push to main | 6-stage gate: actionlint, yamllint, SHA-pinning, permissions, Python syntax, summary |
| **lint-workflows** | PR/push on workflow file changes | actionlint + ghalint validation |
| **security-baseline-check** | PR on workflow file changes | Permissions, SHA-pinning, timeouts, secrets audit |
| **issue-feedback** | Issue comments | Processes `/trufflehog` commands, auto-applies suppression labels |

**Dependabot** is configured for both `pip` and `github-actions` ecosystems (weekly updates).

---

## Scanner Features

The `trufflehog_scanner.py` script processes TruffleHog NDJSON output into actionable GitHub issues and summaries.

| Feature | Description |
|---------|-------------|
| **Severity classification** | Maps 17 detector types to 4 levels: CRITICAL, HIGH, MEDIUM, LOW |
| **Detector-specific remediation** | 12 detector types with step-by-step rotation guides embedded in issues |
| **Hash-based deduplication** | Deterministic SHA-256 of findings prevents duplicate issues across runs |
| **Verified + unverified tracking** | Full breakdown in summaries; issues created only for verified findings |
| **POST-first label strategy** | Creates labels via POST, falls back gracefully on 422 (already exists) |
| **Single-pass NDJSON processing** | One file read for all summary stats and breakdowns |
| **False-positive suppression** | Regex patterns from `.github/trufflehog/false_positives.txt` |
| **Issue feedback loop** | Label-based suppression + comment commands for false-positive/accepted-risk handling |
| **Concurrent issue creation** | ThreadPoolExecutor with configurable worker count |
| **Target repo override** | Consolidate all findings into a single repo (useful for testing) |
| **GitHub App + PAT auth** | Automatic JWT token generation for GitHub App, PAT as fallback |

### Scanner CLI Reference

```
trufflehog_scanner.py [options]
```

| Flag | Description | Default |
|------|-------------|---------|
| `--ndjson PATH` | Input NDJSON file from TruffleHog | (required) |
| `--org NAME` | Organization name (for summary labels) | — |
| `--run-url URL` | Link to the workflow run | — |
| `--scanner-repo OWNER/REPO` | Repository with remediation docs (linked in issues) | — |
| `--dry-run` | Log actions without creating issues or calling GitHub API | `false` |
| `--write-summary` | Write markdown to `$GITHUB_STEP_SUMMARY` | `false` |
| `--include-unverified` | Process unverified findings (appear in summary, not issues) | `false` |
| `--target-repo OWNER/REPO` | Create all issues in this repo instead of per-finding repo | — |
| `--force-create` | Skip deduplication checks and always create issues | `false` |
| `--summary-detailed` | Include per-finding rows in the workflow summary | `false` |
| `--summary-detailed-per-repo N` | Max detailed rows per repo | `10` |
| `--summary-detailed-max N` | Max total detailed rows | `300` |
| `--errors-ndjson PATH` | Path to scan errors NDJSON file | — |
| `--exclude-patterns-file PATH` | File with false-positive regex patterns | — |
| `--labels CSV` | Extra labels to add to issues | — |
| `--title-prefix TEXT` | Prefix for issue titles | `[TruffleHog]` |
| `--max-workers N` | Concurrent issue creation threads | `4` |
| `--log-level LEVEL` | Logging level | `INFO` |
| `--validate` | Validate inputs and exit | `false` |

---

## Issue Creation

Enable with `open_issues: true` in the workflow dispatch UI, or set `DEFAULT_OPEN_ISSUES: true` as a repository variable.

**Title format**: `[TruffleHog] Secrets scan report (<hash>)`
- The `<hash>` is a deterministic SHA-256 of the findings set — same findings always produce the same title.

**Labels**: `security`, `tool:trufflehog`, `<highest-severity>` (e.g. `sec:critical`), `needs-triage`

**Deduplication**: If an issue with the same hash already exists (open or closed), no duplicate is created.

**Issue body includes**:
- Severity badge (🔴 CRITICAL / 🟠 HIGH / 🟡 MEDIUM / 🟢 LOW) and scan timestamp
- Findings table: detector, file, line, commit SHA, verified status, redacted preview, clickable link
- Detector-specific remediation steps (AWS, GCP, Azure, GitHub, Slack, SSH, Stripe, SendGrid, Kubernetes, Docker, Twilio, Private Key)
- Git history cleanup commands (rebase + git-filter-repo)
- **Response Options** section with suppression labels, comment commands, and manual regex instructions

> Issues are created **only for verified findings**. Unverified findings appear in the workflow summary.

### Restricting Issue Creation to Specific Orgs

Set the `TRUFFLEHOG_ISSUES_ORGS` variable (comma-separated) to limit which organizations receive issues. Leave empty or unset to create issues in all scanned orgs.

---

## Issue Feedback Loop

The scanner supports a **closed-loop workflow** where teams can respond to findings directly from the GitHub issue, and those responses are respected on future scans.

### How It Works

1. Scanner creates an issue with a findings hash in the title: `[TruffleHog] Secrets scan report (ab12cd34)`
2. Team reviews the finding and applies a **suppression label** (or uses a comment command)
3. On the next scan, the scanner checks existing issues for suppression labels
4. If the same findings hash has a suppression label, issue creation is **skipped**

### Suppression Labels

These labels are automatically created in each scanned repository:

| Label | Color | When to Use | Scanner Behavior |
|-------|-------|-------------|------------------|
| `trufflehog:false-positive` | ⚪ Gray | Not a real secret (test data, example, docs) | Suppressed on future scans |
| `trufflehog:accepted-risk` | 🟡 Yellow | Real secret, team acknowledges and accepts the risk | Suppressed on future scans |
| `trufflehog:wont-fix` | ⚫ White | Won't remediate (already revoked, low impact) | Suppressed on future scans |
| `trufflehog:remediated` | 🟢 Green | Secret has been rotated and removed from history | Informational only |

### Comment Commands

Team members can also comment on a TruffleHog issue with these commands:

```
/trufflehog false-positive
/trufflehog accepted-risk
/trufflehog wont-fix
/trufflehog remediated
```

The scanner processes these commands on the next scan run and automatically applies the corresponding label. For **real-time** processing, deploy the `issue-feedback.yml` workflow (included in `.github/workflows/`).

### Suppression Flow

```
Scan finds secret  →  Creates issue with hash (ab12cd34)
                          │
                     Team reviews
                          │
               ┌─────────┼───────────┐
               │                       │
        Adds label:              Comments:
   trufflehog:false-positive   /trufflehog false-positive
               │                       │
               └───────┼───────────┘
                       │
              Next scan runs
                       │
           Hash ab12cd34 is in
          suppressed set → SKIP
```

### Three-Tier Suppression

| Tier | Mechanism | Scope | Persistence |
|------|-----------|-------|-------------|
| **1. Issue label** | Add label in GitHub UI | Per-finding (hash-based) | Until label removed |
| **2. Comment command** | `/trufflehog false-positive` | Per-finding (hash-based) | Until label removed |
| **3. Regex allowlist** | Edit `false_positives.txt` | All repos, all scans | Permanent |

---

## Workflow Summary

Every scan run generates a `GITHUB_STEP_SUMMARY` (visible on the Actions run page):

| Section | Content |
|---------|---------|
| **Metrics** | Total findings, verified count, unverified count, excluded FPs, malformed lines |
| **Severity breakdown** | Verified vs. unverified per level (CRITICAL / HIGH / MEDIUM / LOW) |
| **By repository** | Verified, unverified, and total per repo |
| **By detector** | Same breakdown per detector type |
| **Top offenders** | Repos with ≥ 5 verified or ≥ 10 combined findings |
| **Detailed verified** | Clickable file/line links for each verified finding (when `--summary-detailed`) |
| **Detailed unverified** | Collapsible section with all unverified findings |
| **Errors** | API or scan errors encountered during the run |

---

## Configuration Reference

### Workflow Inputs (via Actions UI)

Inputs are available when running the workflow manually from the Actions tab.

#### Basic

| Input | Default | Description |
|-------|---------|-------------|
| `org_names` | (from `TRUFFLEHOG_ORGS` variable) | Comma-separated organizations to scan |
| `open_issues` | `false` | Create GitHub issues for verified findings |
| `issues_creation_orgs` | (empty = all) | Restrict issue creation to these orgs |

#### Scan Strategy

| Input | Options | Default |
|-------|---------|---------|
| `deep_scan` | `true` / `false` | `false` — when `true`, scans ALL branches + full history |
| `branch_strategy` | `all`, `main-only`, `main-recent`, `protected` | `main-recent` |
| `scan_mode` | `full`, `recent`, `incremental` | `recent` |
| `scan_lookback_days` | 1, 3, 7, 14, 30 | `7` |
| `branch_lookback_days` | 7, 14, 30, 60, 90 | `30` |
| `scan_results` | `verified`, `verified,unknown`, `verified,unknown,unverified` | `verified,unknown` |

#### Performance & Sharding

| Input | Options | Default |
|-------|---------|---------|
| `scan_parallel` | 4, 8, 12, 16 | `8` |
| `per_repo_timeout` | 3m, 5m, 10m, 15m | `5m` |
| `shard_cap` | 5, 10, 15, 20 | `10` |
| `repos_per_shard` | 25, 50, 75, 100 | `50` |
| `adaptive_timeout` | `true` / `false` | `true` (2-15m based on repo size) |
| `skip_stale_branches` | `true` / `false` | `true` |

### Repository Variables

Set in **Settings → Secrets and variables → Actions → Variables**. These override hardcoded defaults; manual inputs override variables.

**Priority**: Manual Input > Repository Variable > Hardcoded Default

| Variable | Default | Description |
|----------|---------|-------------|
| `TRUFFLEHOG_ORGS` | — | Default orgs to scan |
| `TRUFFLEHOG_ISSUES_ORGS` | (empty = all) | Allow-list for issue creation |
| `TRUFFLEHOG_VERSION` | `3.92.5` | TruffleHog version |
| `TRUFFLEHOG_IMAGE` | `ghcr.io/trufflesecurity/trufflehog:3.92.5` | Docker image |
| `TRUFFLEHOG_CACHE_FILE` | `.trufflehog_cache/trufflehog-3.92.5.tar` | Image cache path |
| `GITHUB_API` | `https://api.github.com` | API endpoint (for GitHub Enterprise) |
| `DEFAULT_SCAN_RESULTS` | `verified,unknown` | Default results filter |
| `DEFAULT_BRANCH_STRATEGY` | `main-recent` | Default branch strategy |
| `DEFAULT_SCAN_MODE` | `recent` | Default scan mode |
| `DEFAULT_BRANCH_LOOKBACK_DAYS` | `30` | Default branch lookback |
| `DEFAULT_SCAN_LOOKBACK_DAYS` | `7` | Default scan lookback |
| `DEFAULT_OPEN_ISSUES` | `false` | Auto-create issues |
| `DEFAULT_DEEP_SCAN` | `false` | Enable deep scan by default |
| `SHARD_CAP` | `10` | Max shards per org |
| `REPOS_PER_SHARD` | `50` | Target repos per shard |
| `SCAN_PAR` | `8` | Concurrent scans per shard |
| `SIZE_BASED_SHARDING` | `false` | Use size-based assignment |
| `PER_REPO_TIMEOUT` | `5m` | Per-repo timeout |
| `ADAPTIVE_TIMEOUT` | `true` | Enable adaptive timeouts |
| `MAX_REPO_SIZE_KB` | `0` (unlimited) | Skip repos exceeding this size |
| `MAX_FINDING_LOG_LINES` | `300` | Max preview lines |
| `ALLOW_VER_OVERLAP` | `true` | Allow verification overlap |
| `EXCLUDE_GENERIC` | `false` | Exclude generic detectors |

### Cron Schedule

Default: **Every Sunday at 6 PM UTC (10 AM PST)**

```yaml
schedule:
  - cron: '0 18 * * 0'
```

Common alternatives:
- Daily at midnight UTC: `'0 0 * * *'`
- Every Monday 9 AM UTC: `'0 9 * * 1'`
- Twice weekly (Mon/Thu): `'0 0 * * 1,4'`

### False-Positive Suppression

Add regex patterns to `.github/trufflehog/false_positives.txt` (one per line, `#` for comments):

```
# Placeholder credentials in docs/examples
\bexample(_|-)?(user|username|token|password)\b
postgres://username:password@hostname:\d+/

# Test fixture files
file=.*test/fixtures/.*
```

Patterns match against: `detector=<name> | verified=<bool> | repo=<owner/name> | file=<path> | redacted=<token>`

See [CONFIGURATION.md](CONFIGURATION.md) for preset examples (daily, weekly, deep scan, large orgs).

---

## Running Locally

### Dry-Run (No Issues Created)

```bash
# Install dependencies
pip install -r requirements.txt

# Run scanner against an NDJSON file
python3 trufflehog_scanner.py \
  --ndjson findings.ndjson \
  --org my-org \
  --dry-run \
  --write-summary \
  --include-unverified
```

### Generate NDJSON from a Repo

```bash
docker run --rm -v "$PWD:/work" \
  ghcr.io/trufflesecurity/trufflehog:3.92.5 \
  github --repo "https://github.com/owner/repo" --token "$GH_PAT" --json \
  > findings.ndjson
```

### Force Issue Creation (Skip Dedup)

```bash
python3 trufflehog_scanner.py \
  --ndjson findings.ndjson \
  --org my-org \
  --force-create \
  --target-repo owner/test-repo
```

### Consolidate into a Single Repo

```bash
python3 trufflehog_scanner.py \
  --ndjson findings.ndjson \
  --org my-org \
  --target-repo owner/security-findings \
  --include-unverified \
  --write-summary
```

---

## Testing

### Unit Tests

```bash
pip install -r requirements.txt
python3 -m pytest test_trufflehog_scanner.py -v
```

**142 tests** covering: severity classification, label generation, hash deduplication, NDJSON loading,
issue body generation, summary output, GHClient API calls, rate limiting, multi-org authentication, and end-to-end `process_repo` flows.

### Self-Scan Workflow

The `self-scan` workflow runs on every push and scans this repository's `test/fixtures/` directory.
It validates:
- TruffleHog detects the expected test secrets (minimum 20 findings)
- The 4-job pipeline (plan → scan → summarize → workflow-summary) completes successfully
- The scanner script processes findings correctly with `--include-unverified --target-repo --force-create`

Trigger manually: **Actions → Self-Scan Test → Run workflow**

### Test Fixtures

The `test/fixtures/` directory contains **intentional fake secrets** across 6 file types:

| File | Secrets | Types |
|------|---------|-------|
| `secrets.txt` | AWS keys, GitHub tokens, Slack tokens, DB credentials | Multi-format plaintext |
| `sample_script.py` | AWS, GitHub, Stripe, SendGrid keys | Python variables |
| `dotenv.sample` | Database, API keys, JWT secrets | `.env` format |
| `gcp-service-account.json` | GCP service account private key | JSON key file |
| `test_rsa_key.pem` | RSA private key | PEM format |
| `../test-config.json` | AWS, GitHub, Stripe, Slack, GCP, DB credentials | JSON config |

See [TEST_FIXTURES.md](TEST_FIXTURES.md) for full details.

---

## Remediating Discovered Secrets

When the scanner finds secrets:

1. **Rotate/revoke immediately** — assume every detected secret is compromised
2. **Remove from current files** — replace with environment variables or secret manager references
3. **Purge from git history** — use `git rebase -i` (recent) or `git-filter-repo` (old commits)
4. **Force push** — coordinate with team, then `git push --force-with-lease`
5. **Verify cleanup** — re-run TruffleHog to confirm removal

Created issues include detector-specific rotation steps for **12 detector types** (AWS IAM, GCP service accounts, Azure, GitHub tokens, Slack, SSH, Stripe, SendGrid, Kubernetes, Docker, Twilio, Private Key).

See [REMEDIATION.md](REMEDIATION.md) for the complete guide with commands.

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| **Scans too slow** | `branch_strategy: main-only`, increase `scan_parallel`, enable `adaptive_timeout` |
| **Missing findings** | `deep_scan: true`, `scan_mode: full`, increase lookback windows |
| **Rate limits** | Reduce `scan_parallel` to 4, lower `shard_cap`, stagger org scans |
| **Timeouts** | Enable `adaptive_timeout`, increase `per_repo_timeout`, set `max_repo_size_kb` |
| **401/403 errors** | Verify token scopes (`repo` + `read:org`), check expiration, confirm org membership |
| **No issues created** | Set `open_issues: true`, verify token has Issues (Read/Write), check `TRUFFLEHOG_ISSUES_ORGS` |
| **Verify setup works** | Run `self-scan` workflow manually — validates TruffleHog + scanner end-to-end |

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for detailed solutions and performance tuning.

---

## NIST 800-53 Rev 5 Compliance Coverage

This scanner directly supports or contributes to the following [NIST SP 800-53 Rev 5](https://csrc.nist.gov/publications/detail/sp/800-53/rev-5/final) security controls.

### Credential & Authenticator Management

| Control | Name | How This Scanner Addresses It |
|---------|------|-------------------------------|
| **IA-5** | Authenticator Management | Detects exposed credentials (17 detector types: AWS, GCP, Azure, GitHub, Slack, SSH, Stripe, SendGrid, etc.) across all branches and full git history of 15+ organizations |
| **IA-5(1)** | Password-Based Authentication | Identifies hardcoded passwords and database connection strings (Postgres, MongoDB) in source code and config files |
| **IA-5(7)** | No Embedded Unencrypted Static Authenticators | Scans for API keys, tokens, and secrets stored in plaintext — the primary purpose of this tool |

### System Monitoring & Alerting

| Control | Name | How This Scanner Addresses It |
|---------|------|-------------------------------|
| **SI-4** | System Monitoring | Weekly scheduled scans (cron) across all org repos; push-triggered self-scan validates scanner integrity continuously |
| **SI-4(2)** | Automated Tools and Mechanisms for Real-Time Analysis | Automated GitHub Actions workflow with parallel sharded scanning; no manual intervention required |
| **SI-4(5)** | System-Generated Alerts | Auto-creates GitHub issues with severity labels (`sec:critical`, `sec:high`, `sec:medium`, `sec:low`) and `needs-triage` for verified findings |
| **SI-4(12)** | Automated Organization-Generated Alerts | Issues include detector-specific remediation steps, file/line/commit links, and rotation guides for 13 credential types |

### Vulnerability & Risk Assessment

| Control | Name | How This Scanner Addresses It |
|---------|------|-------------------------------|
| **RA-5** | Vulnerability Monitoring and Scanning | Scans source code repositories for exposed secrets — a critical vulnerability class; configurable scan depth (full history, recent, incremental) |
| **RA-5(2)** | Update Vulnerabilities to Be Scanned | Dependabot keeps TruffleHog version and all GitHub Actions dependencies updated weekly |
| **RA-5(5)** | Privileged Access | GitHub App authentication with org-scoped tokens (`for_org()`) uses minimum required permissions; workflows enforce `permissions:` declarations |

### Incident Response

| Control | Name | How This Scanner Addresses It |
|---------|------|-------------------------------|
| **IR-4** | Incident Handling | Every issue includes step-by-step remediation, credential rotation, git history cleanup. Issue feedback loop allows teams to mark findings as false-positive, accepted-risk, or remediated via labels/comment commands |
| **IR-5** | Incident Monitoring | Workflow summaries provide per-org and per-repo breakdowns of verified/unverified findings with severity classification |
| **IR-6** | Incident Reporting | Issues are created directly in the affected repository with full context: detector type, file path, line number, commit SHA, blame link, and scan timestamp |

### Configuration & Change Management

| Control | Name | How This Scanner Addresses It |
|---------|------|-------------------------------|
| **CM-3** | Configuration Change Control | PR validation pipeline (5-stage gate: actionlint, yamllint, SHA-pinning, permissions audit, Python syntax) enforces change control on all workflow modifications |
| **CM-6** | Configuration Settings | Detects secrets in configuration files (`.env`, `settings.php`, `docker-compose.yml`, JSON configs); false-positive patterns are version-controlled |
| **CM-14** | Signed Components | All GitHub Actions are SHA-pinned (40-char commit hash); PR validation rejects unpinned references |

### Audit & Accountability

| Control | Name | How This Scanner Addresses It |
|---------|------|-------------------------------|
| **AU-2** | Event Logging | Structured logging with timestamp, log level, thread name, and function name for every API call, issue creation, and dedup decision |
| **AU-6** | Audit Record Review | Workflow summaries with severity breakdown tables, per-repo findings, top offenders, and error reports; findings stored as downloadable NDJSON artifacts |
| **AU-6(1)** | Automated Process Integration | Summary written to `GITHUB_STEP_SUMMARY` and automatically linked from the Actions run page; issues auto-labeled for triage queues |
| **AU-12** | Audit Record Generation | Per-repo scan diagnostics (`SCAN_SUMMARY.txt`), structured error NDJSON (`errors-{ORG}.ndjson`), and per-shard troubleshoot bundles captured as artifacts |

### Access Control & Least Privilege

| Control | Name | How This Scanner Addresses It |
|---------|------|-------------------------------|
| **AC-6** | Least Privilege | Workflows declare minimal `permissions:` (`contents: read`, `issues: write`); security baseline check flags `write-all`; `persist-credentials: false` on all checkouts |
| **AC-6(1)** | Authorize Access to Security Functions | GitHub App tokens are org-scoped (minted per-org via `for_org()`); PAT fallback uses `repo` + `read:org` only |

### Information Protection

| Control | Name | How This Scanner Addresses It |
|---------|------|-------------------------------|
| **SC-28** | Protection of Information at Rest | Identifies unencrypted secrets stored in repositories that should use vaults, KMS, or environment variables |
| **SC-28(1)** | Cryptographic Protection | Flags credentials that should be managed by secret managers (AWS KMS, GCP Secret Manager, Azure Key Vault, HashiCorp Vault) |

### System & Services Acquisition

| Control | Name | How This Scanner Addresses It |
|---------|------|-------------------------------|
| **SA-11** | Developer Testing and Evaluation | 167 unit tests; self-scan workflow validates scanner on every push against intentional test fixtures (≥20 expected findings) |
| **SA-15** | Development Process, Standards, and Tools | Supply chain protections: SHA-pinned actions, Dependabot, Semgrep SAST, security baseline checks, PR validation gates |

### Supply Chain Risk Management

| Control | Name | How This Scanner Addresses It |
|---------|------|-------------------------------|
| **SR-3** | Supply Chain Controls and Processes | SHA-pinned GitHub Actions validated against trusted org allowlist (`actions`, `github`, `docker`, `trufflesecurity`); Dependabot for dependency updates |
| **SR-4** | Provenance | Docker image pinned to exact version tag; all dependency versions tracked in `requirements.txt` |
| **SR-11** | Component Authenticity | PR validation fails if any action reference uses tag-based versioning instead of SHA; security baseline enforces the same |

---

## Documentation Index

| Document | Description |
|----------|-------------|
| [SETUP.md](SETUP.md) | Authentication setup (GitHub App overview, PAT creation, org configuration) |
| [GITHUB_APP_SETUP.md](GITHUB_APP_SETUP.md) | GitHub App authentication — creation, permissions, installation |
| [CONFIGURATION.md](CONFIGURATION.md) | Preset examples (daily, weekly, deep scan, large orgs, baseline) |
| [REMEDIATION.md](REMEDIATION.md) | Step-by-step secrets remediation with git-filter-repo |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | Performance tuning, common issues, monitoring |
| [TEST_FIXTURES.md](TEST_FIXTURES.md) | Test secrets documentation and validation |

## Resources

- [TruffleHog OSS](https://github.com/trufflesecurity/trufflehog)
- [NIST SP 800-53 Rev 5 Control Catalog](https://csrc.nist.gov/publications/detail/sp/800-53/rev-5/final)
- [GitHub Actions Documentation](https://docs.github.com/en/actions)
- [Removing Sensitive Data from a Repository](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository)
- [GitHub Secret Scanning](https://docs.github.com/en/code-security/secret-scanning)