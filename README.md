# TruffleHog Organization Scanner

> **Automated secret scanning for GitHub organizations with 40-80% faster performance.**

Production-ready GitHub Actions workflow for scanning multiple organizations with TruffleHog OSS. Features intelligent sharding, parallel processing, and automated issue creation.

## Table of Contents

| Document | Description |
|----------|-------------|
| [README.md](README.md) | Overview, quick start, and architecture |
| [SETUP.md](SETUP.md) | PAT creation, org configuration |
| [GITHUB_APP_SETUP.md](GITHUB_APP_SETUP.md) | GitHub App authentication setup |
| [CONFIGURATION.md](CONFIGURATION.md) | Workflow inputs, examples, tuning |
| [REMEDIATION.md](REMEDIATION.md) | Secrets remediation guide |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | Performance tuning, issue resolution |
| [TEST_FIXTURES.md](TEST_FIXTURES.md) | Test secrets documentation |

## Quick Start

1. **Add authentication**: Go to **Settings → Secrets and variables → Actions → Secrets**
   - **Option A (Recommended)**: GitHub App - Add `GH_APP_ID`, `GH_APP_PRIVATE_KEY`, `GH_APP_INSTALLATION_ID` ([setup guide](GITHUB_APP_SETUP.md))
   - **Option B (Fallback)**: PAT - Add `GH_PAT` with `repo` + `read:org` scopes ([setup guide](SETUP.md))
2. **Set variable**: Go to **Settings → Secrets and variables → Actions → Variables**
   - Add `TRUFFLEHOG_ORGS=org1,org2,org3`
3. **Run**: Actions → TruffleHog Org Scan → Run workflow

Done! Runs **40-80% faster** with optimized defaults. See **Configuration** section below for details.

## Features

**Performance**
- Smart branch filtering (default + recent branches only)
- Time-based scanning (last 7 days vs full history)
- Adaptive timeout (2-15m based on repo size)
- Parallel sharding (up to 20 shards per org)

**Scanning Modes**
- **Standard**: Fast scan of recent changes (default)
- **Deep Scan**: All branches + full history (monthly audits)
- **Custom**: 10 configurable parameters

**Operations**
- Multi-org scanning with verified-only filtering
- Automated GitHub issue creation
- Regex-based false positive suppression
- Comprehensive summaries with metrics

## Performance

| Org Size | Before | After | Improvement |
|----------|--------|-------|-------------|
| 25 repos | 3-5 min | 1-2 min | 60% faster |
| 150 repos | 8-12 min | 2-3 min | 75% faster |
| 500 repos | 15-25 min | 3-5 min | 80% faster |
| 1500 repos | 30-60 min | 5-10 min | 83% faster |

## Configuration

### Repository Setup

Configure these in your GitHub repository settings:

#### Secrets (Required)
Go to **Settings → Secrets and variables → Actions → New repository secret**:

**Authentication Method 1: GitHub App** (Recommended for enterprise)
- **`GH_APP_ID`**: GitHub App ID
- **`GH_APP_PRIVATE_KEY`**: GitHub App private key (PEM format)
- **`GH_APP_INSTALLATION_ID`**: GitHub App installation ID
- See **[GITHUB_APP_SETUP.md](GITHUB_APP_SETUP.md)** for GitHub App setup instructions
- Benefits: Higher rate limits (15k/hour for enterprise), better security, org-level management

**Authentication Method 2: Personal Access Token** (Fallback)
- **`GH_PAT`**: GitHub Personal Access Token with `repo` + `read:org` scopes
- See **[SETUP.md](SETUP.md)** for PAT creation instructions
- Used automatically if GitHub App credentials are not available

#### Variables (Optional)
Go to **Settings → Secrets and variables → Actions → Variables tab → New repository variable**:

Priority: **Manual Input** > **Repository Variable** > **Hardcoded Default**

Key variables:
| Variable | Default | Description |
|----------|---------|-------------|
| `TRUFFLEHOG_ORGS` | trufflesecurity,gitleaks | Organizations to scan |
| `DEFAULT_REPO_VISIBILITY` | public | Repo visibility (public/private/all) |
| `DEFAULT_SCAN_RESULTS` | all | Results filter |
| `TRUFFLEHOG_ISSUES_ORGS` | (empty) | Orgs for issue creation |

See **[CONFIGURATION.md](CONFIGURATION.md)** for complete variable reference.

#### Cron Schedule
Default: **Every Sunday at 6 PM UTC**

To modify, edit `.github/workflows/trufflehog-org-scan.yml`:
```yaml
schedule:
  - cron: '0 18 * * 0'  # Sunday 6 PM UTC
```

Common schedules:
- Daily at midnight: `'0 0 * * *'`
- Every Monday 9 AM: `'0 9 * * 1'`
- Twice weekly (Mon/Thu): `'0 0 * * 1,4'`

#### False Positives
Configure regex patterns to exclude known false positives:

**File**: `.github/trufflehog/false_positives.txt`

Add newline-delimited regex patterns (lines starting with `#` are comments):
```
# Example placeholder patterns
\bexample(_|-)?(user|username|token|password)\b
postgres://username:password@hostname:\d+/
```

Patterns match against: `detector=... | verified=... | repo=... | file=... | redacted=...`

### Workflow Inputs

11 configurable inputs via Actions UI:

**Basic**: `org_names`, `open_issues`, `issues_creation_orgs`, `repo_visibility`
**Scan Mode**: `deep_scan` (enables full branch/history scan)
**Performance**: `scan_parallel`, `per_repo_timeout`, `scan_results`
**Sharding**: `shard_cap`, `repos_per_shard`
**Strategy**: `branch_strategy`, `scan_mode`, `branch_lookback_days`, `scan_lookback_days`
**Advanced**: `adaptive_timeout`, `skip_stale_branches`, `max_finding_log_lines`

See **[CONFIGURATION.md](CONFIGURATION.md)** for examples (daily, weekly, deep scans, large orgs).

### Default Behavior

| Setting | Default | Notes |
|---------|---------|-------|
| Organizations | trufflesecurity, gitleaks | Public test repos for validation |
| Visibility | public | No org membership required |
| Results | all | Shows verified + unverified findings |
| Branch Strategy | main-recent | Default + branches active in last 30 days |
| Scan Mode | recent | Last 7 days of commits |

**Public-only scans** skip org membership checks, allowing you to scan any public org.

See **[CONFIGURATION.md](CONFIGURATION.md)** for all defaults and override options.

## Architecture

```mermaid
flowchart TD
    Start([Workflow Triggered]) --> ResolveMatrix[1. resolve-matrix<br/>Parse org names from input/variable]

    ResolveMatrix --> Plan[2. plan<br/>Count repos per org<br/>Calculate optimal shard count<br/>Output task matrix]

    Plan --> ScanOrg1[3a. scan-org shard 1]
    Plan --> ScanOrg2[3b. scan-org shard 2]
    Plan --> ScanOrg3[3c. scan-org shard N]

    ScanOrg1 --> |Load TruffleHog image cached<br/>Enumerate repos for shard<br/>Scan repos concurrently<br/>Upload per-shard artifacts| Summarize
    ScanOrg2 --> |Load TruffleHog image cached<br/>Enumerate repos for shard<br/>Scan repos concurrently<br/>Upload per-shard artifacts| Summarize
    ScanOrg3 --> |Load TruffleHog image cached<br/>Enumerate repos for shard<br/>Scan repos concurrently<br/>Upload per-shard artifacts| Summarize

    Summarize[4. summarize-org per org<br/>Download all shard artifacts<br/>Merge NDJSON files<br/>Filter false positives<br/>Generate summary<br/>Create issues optional]

    Summarize --> WorkflowSummary[5. workflow-summary<br/>Show configuration used<br/>Display performance metrics<br/>Link to per-org summaries]

    WorkflowSummary --> End([Workflow Complete])

    style Start fill:#2e7d32,stroke:#1b5e20,color:#fff
    style End fill:#2e7d32,stroke:#1b5e20,color:#fff
    style ResolveMatrix fill:#1976d2,stroke:#0d47a1,color:#fff
    style Plan fill:#f57c00,stroke:#e65100,color:#fff
    style ScanOrg1 fill:#7b1fa2,stroke:#4a148c,color:#fff
    style ScanOrg2 fill:#7b1fa2,stroke:#4a148c,color:#fff
    style ScanOrg3 fill:#7b1fa2,stroke:#4a148c,color:#fff
    style Summarize fill:#c2185b,stroke:#880e4f,color:#fff
    style WorkflowSummary fill:#0097a7,stroke:#006064,color:#fff
```

**Sharding**: Deterministic modulo distribution (default) or size-based round-robin for load balancing.

## Issue Creation

Set `open_issues: true` to enable automated issue creation.

**Format**: `[TruffleHog] Secrets scan report - YYYY-MM-DD`
**Deduplication**: Checks for existing issues within 7 days
**Labels**: `security`, `secrets`, `tool:trufflehog`, `needs-triage`, detector-specific, severity-based

## Remediation

When secrets are found:

1. **Rotate credentials immediately** (assume compromised)
2. Remove from current files
3. Purge from git history (rebase for recent, git-filter-repo for old)
4. Force push and notify team
5. Add prevention measures

See **[REMEDIATION.md](REMEDIATION.md)** for complete guide with git-filter-repo instructions.

## Testing

**Self-Test** (default): Run workflow without setting `TRUFFLEHOG_ORGS` - scans public test repos:
- `trufflesecurity/test_keys` - AWS creds, SSH key, auth URI
- `gitleaks/fake-leaks` - Various test secrets

**Self-Scan**: Runs on every push, scans this repo's test fixtures in `test/fixtures/`

**Manual Run**: Actions → TruffleHog Org Scan → Run workflow

**Local Testing**:
```bash
# Test scanner
python3 trufflehog_scanner.py --ndjson findings.ndjson --org test-org --dry-run

# Generate test data
docker run --rm -v "$PWD:/work" ghcr.io/trufflesecurity/trufflehog:3.90.6 \
  github --repo "https://github.com/owner/repo" --token "$GH_PAT" --json
```

**Workflow Summary**: Both workflows display findings breakdown (Total/Verified/Unverified) and top detector types in the GitHub Actions summary.

## Troubleshooting

**Scans too slow?** Reduce branch scope, increase parallelism
**Missing findings?** Use `deep_scan: true`, expand time windows
**Rate limits?** Reduce concurrency, lower shard count
**Timeouts?** Enable `adaptive_timeout`, increase timeout

See **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)** for detailed solutions.

## Documentation

**Setup & Configuration**
- [SETUP.md](SETUP.md) - PAT creation, org config, troubleshooting
- [CONFIGURATION.md](CONFIGURATION.md) - Input reference, examples

**Operations**
- [REMEDIATION.md](REMEDIATION.md) - Secrets remediation guide
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) - Performance tuning, issue resolution

**Technical Reference**
- [PERMISSION_ANALYSIS.md](PERMISSION_ANALYSIS.md) - API endpoints, permissions
- [TEST_FIXTURES.md](TEST_FIXTURES.md) - Test secrets documentation

## Resources

- [TruffleHog OSS](https://github.com/trufflesecurity/trufflehog)
- [GitHub Actions Docs](https://docs.github.com/en/actions)
- [Fine-Grained PAT](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens#creating-a-fine-grained-personal-access-token)
- [Classic PAT](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens#creating-a-personal-access-token-classic)
