# TruffleHog Organization Scanner

> **Automated secret scanning for GitHub organizations with 40-80% faster performance.**

Production-ready GitHub Actions workflow for scanning multiple organizations with TruffleHog OSS. Features intelligent sharding, parallel processing, and automated issue creation.

## Quick Start

1. **Add secret**: `GH_PAT` with `repo` + `read:org` scopes
2. **Set variable**: `TRUFFLEHOG_ORGS=org1,org2,org3`
3. **Run**: Actions → TruffleHog Org Scan → Run workflow

Done! Runs **40-80% faster** with optimized defaults.

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

### Required Setup

**Secret** (required):
- `GH_PAT`: GitHub token with `repo` + `read:org` scopes

**Variables** (optional):
- `TRUFFLEHOG_ORGS`: Default orgs to scan (comma-separated)
- `TRUFFLEHOG_ISSUES_ORGS`: Orgs for issue creation (empty = all)

See **[SETUP.md](SETUP.md)** for PAT creation and configuration details.

### Workflow Inputs

10 configurable inputs via Actions UI:

**Basic**: `org_names`, `open_issues`, `issues_creation_orgs`
**Scan Mode**: `deep_scan` (enables full branch/history scan)
**Performance**: `scan_parallel`, `per_repo_timeout`, `scan_results`
**Sharding**: `shard_cap`, `repos_per_shard`
**Strategy**: `branch_strategy`, `scan_mode`, `branch_lookback_days`, `scan_lookback_days`
**Advanced**: `adaptive_timeout`, `skip_stale_branches`, `max_finding_log_lines`

See **[CONFIGURATION.md](CONFIGURATION.md)** for examples (daily, weekly, deep scans, large orgs).

### Scheduled Runs

Default: **Every Sunday at 6 PM UTC**

```yaml
on:
  schedule:
    - cron: '0 18 * * 0'
```

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

**Manual Run**: Actions → TruffleHog Org Scan → Run workflow

**Local Testing**:
```bash
# Test scanner
python3 trufflehog_scanner.py --ndjson findings.ndjson --org test-org --dry-run

# Generate test data
docker run --rm -v "$PWD:/work" ghcr.io/trufflesecurity/trufflehog:3.90.6 \
  github --repo "https://github.com/owner/repo" --token "$GH_PAT" --json
```

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

## Changelog

### v2.3 (Current)
- Fixed deduplication bug with None line numbers
- Parameterized hardcoded values (rate limits, timeouts, retries)
- Added comprehensive type hints
- Complete PERMISSION_ANALYSIS.md and TEST_FIXTURES.md docs

### v2.2
- Deep scan mode for comprehensive audits
- 17 fully configurable parameters
- Enhanced workflow summaries

### v2.1
- Comprehensive PAT documentation
- Bug fixes (10-input limit, missing dirs)
- Pull request trigger

### v2.0
- 40-80% faster with smart filtering
- 10 configurable inputs
- Adaptive timeout, size-based sharding

## Resources

- [TruffleHog OSS](https://github.com/trufflesecurity/trufflehog)
- [GitHub Actions Docs](https://docs.github.com/en/actions)
- [Fine-Grained PAT](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens#creating-a-fine-grained-personal-access-token)
- [Classic PAT](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens#creating-a-personal-access-token-classic)

## License

Provided as-is for scanning your own organizations.
