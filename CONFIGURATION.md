# Configuration Guide

Complete configuration reference for TruffleHog Organization Scanner.

## Table of Contents
- [Workflow Inputs](#workflow-inputs)
- [Hardcoded Settings](#hardcoded-settings)
- [Configuration Examples](#configuration-examples)

## Workflow Inputs

The workflow accepts the following inputs via the Actions UI (Run workflow button). All inputs are fully configurable.

### Basic Configuration
| Input | Options | Default | Description |
|-------|---------|---------|-------------|
| `org_names` | comma-separated | (from var) | Organizations to scan (supports single or multiple orgs) |
| `open_issues` | true/false | false | Create GitHub issues for findings |
| `issues_creation_orgs` | comma-separated | (empty) | Allowlist of orgs where issues should be created (empty = all orgs) |

**Note**: The `org_names` input takes priority over the `TRUFFLEHOG_ORGS` repository variable. The `issues_creation_orgs` allowlist controls which organizations will have issues created when `open_issues` is true.

### Deep Scan Mode
| Input | Options | Default | Description |
|-------|---------|---------|-------------|
| `deep_scan` | true/false | false | **Deep scan mode**: Scans ALL branches and ALL commits (overrides branch_strategy and scan_mode) |

**Important**: When `deep_scan` is enabled:
- Branch strategy is automatically set to "all" (scans every branch)
- Scan mode is automatically set to "full" (scans entire commit history)
- Stale branch skipping is disabled (scans branches older than 90 days)
- Use this for monthly comprehensive audits or initial baseline scans

### Performance Tuning
| Input | Options | Default | Description |
|-------|---------|---------|-------------|
| `scan_parallel` | 4, 8, 12, 16 | 8 | Concurrent repos per shard |
| `per_repo_timeout` | 3m, 5m, 10m, 15m | 5m | Timeout per repository |
| `scan_results` | verified, verified+unknown, all | verified+unknown | Results filter |

### Sharding Configuration
| Input | Options | Default | Description |
|-------|---------|---------|-------------|
| `shard_cap` | 5, 10, 15, 20 | 10 | Maximum shards per org |
| `repos_per_shard` | 25, 50, 75, 100 | 50 | Target repos per shard |

### Scan Strategies (Overridden by deep_scan)
| Input | Options | Default | Description |
|-------|---------|---------|-------------|
| `branch_strategy` | all, main-only, main-recent, protected | main-recent | Branch scanning strategy |
| `scan_mode` | full, recent, incremental | recent | Commit scanning mode |
| `branch_lookback_days` | 7, 14, 30, 60, 90 | 30 | Days to look back for "recent" branches (main-recent strategy) |
| `scan_lookback_days` | 1, 3, 7, 14, 30 | 7 | Days to scan in recent/incremental mode |

### Advanced Configuration
| Input | Options | Default | Description |
|-------|---------|---------|-------------|
| `adaptive_timeout` | true/false | true | Automatically adjusts timeout by repo size (2-15m) |
| `skip_stale_branches` | true/false | true | Skips branches not updated in 90+ days |
| `max_finding_log_lines` | 50, 100, 300, 500, 1000 | 300 | Maximum sanitized finding lines to log |

## Hardcoded Settings

These settings have fixed values optimized for most use cases:

| Setting | Value | Description |
|---------|-------|-------------|
| `trufflehog_version` | 3.90.6 | TruffleHog Docker image version (hardcoded) |
| `enable_size_based_sharding` | false | Size-based load balancing (disabled by default) |
| `max_repo_size_kb` | 0 | Repo size limit in KB (0 = unlimited) |

To change these settings, modify the workflow file directly (`.github/workflows/trufflehog-org-scan.yml`).

## Configuration Examples

These examples show how to configure workflow inputs for different scanning scenarios.

### Daily Scans (Maximum Speed)
```yaml
branch_strategy: main-only
scan_mode: recent
scan_lookback_days: 1
scan_parallel: 16
per_repo_timeout: 3m
adaptive_timeout: true
```
**Expected runtime**: 2-5 min for most orgs
**Use case**: Quick daily checks for new secrets in main branch only

### Weekly Scans (Recommended)
```yaml
branch_strategy: main-recent
scan_mode: recent
scan_lookback_days: 7
branch_lookback_days: 30
scan_parallel: 12
adaptive_timeout: true
skip_stale_branches: true
```
**Expected runtime**: 3-10 min for most orgs (RECOMMENDED)
**Use case**: Regular scanning of active development branches
**Note**: Uses optimized defaults

### Monthly Deep Scans (Comprehensive)
```yaml
deep_scan: true
per_repo_timeout: 10m
scan_parallel: 8
adaptive_timeout: true
```
**Expected runtime**: 15-45 min for most orgs
**Use case**: Full audit of all branches and complete commit history
**Note**: `deep_scan: true` automatically sets `branch_strategy: all`, `scan_mode: full`, and `skip_stale_branches: false`

### Custom Deep Scan (Without deep_scan Flag)
```yaml
branch_strategy: all
scan_mode: full
per_repo_timeout: 10m
scan_parallel: 8
branch_lookback_days: 90
scan_lookback_days: 30
skip_stale_branches: false
adaptive_timeout: true
```
**Expected runtime**: 15-45 min for most orgs
**Use case**: When you need granular control over deep scanning parameters

### Large Organization (1000+ repos)
```yaml
repos_per_shard: 100
shard_cap: 20
scan_parallel: 16
branch_strategy: main-only
scan_mode: recent
scan_lookback_days: 3
per_repo_timeout: 5m
adaptive_timeout: true
```
**Expected runtime**: 5-12 min
**Use case**: Fast scanning of large organizations
**Note**: To skip large repos, edit workflow file to change `max_repo_size_kb` from 0

### Initial Baseline Scan (First Run)
```yaml
deep_scan: true
per_repo_timeout: 15m
scan_parallel: 4
adaptive_timeout: true
max_finding_log_lines: 1000
```
**Expected runtime**: 30-90 min for most orgs
**Use case**: Comprehensive initial scan to establish baseline
**Note**: Lower parallelism to avoid rate limits on first run

---

**See also:**
- [Main README](README.md) - Overview and quick start
- [Setup Guide](SETUP.md) - Detailed setup instructions
- [Remediation Guide](REMEDIATION.md) - How to remediate discovered secrets
- [Troubleshooting](TROUBLESHOOTING.md) - Performance tuning and troubleshooting
