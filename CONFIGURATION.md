# Configuration Guide

Configuration reference for TruffleHog Organization Scanner.

## Workflow Inputs

11 configurable inputs via Actions UI (Run workflow button).

### Basic
| Input | Options | Default | Description |
|-------|---------|---------|-------------|
| `org_names` | comma-separated | trufflesecurity,gitleaks | Organizations to scan |
| `open_issues` | true/false | false | Create GitHub issues |
| `issues_creation_orgs` | comma-separated | (empty) | Orgs for issue creation (empty = all) |
| `repo_visibility` | public, all, private | public | Filter repos by visibility |

### Deep Scan
| Input | Options | Default | Description |
|-------|---------|---------|-------------|
| `deep_scan` | true/false | false | Scans ALL branches + full history |

**When enabled**: Sets `branch_strategy: all`, `scan_mode: full`, `skip_stale_branches: false`

### Performance
| Input | Options | Default |
|-------|---------|---------|
| `scan_parallel` | 4, 8, 12, 16 | 8 |
| `per_repo_timeout` | 3m, 5m, 10m, 15m | 5m |
| `scan_results` | all, verified+unknown, verified | all |

### Sharding
| Input | Options | Default |
|-------|---------|---------|
| `shard_cap` | 5, 10, 15, 20 | 10 |
| `repos_per_shard` | 25, 50, 75, 100 | 50 |

### Scan Strategy
| Input | Options | Default |
|-------|---------|---------|
| `branch_strategy` | all, main-only, main-recent, protected | main-recent |
| `scan_mode` | full, recent, incremental | recent |
| `branch_lookback_days` | 7, 14, 30, 60, 90 | 30 |
| `scan_lookback_days` | 1, 3, 7, 14, 30 | 7 |

### Advanced
| Input | Options | Default |
|-------|---------|---------|
| `adaptive_timeout` | true/false | true |
| `skip_stale_branches` | true/false | true |
| `max_finding_log_lines` | 50-1000 | 300 |

## Hardcoded Settings

Modify `.github/workflows/trufflehog-org-scan.yml` to change:

| Setting | Value |
|---------|-------|
| `trufflehog_version` | 3.90.6 |
| `enable_size_based_sharding` | false |
| `max_repo_size_kb` | 0 (unlimited) |

## Configuration Examples

### Daily Scans (Maximum Speed)
```yaml
branch_strategy: main-only
scan_mode: recent
scan_lookback_days: 1
scan_parallel: 16
per_repo_timeout: 3m
```
**Runtime**: 2-5 min | **Use case**: Quick daily checks

### Weekly Scans (Recommended)
```yaml
branch_strategy: main-recent
scan_mode: recent
scan_lookback_days: 7
branch_lookback_days: 30
scan_parallel: 12
skip_stale_branches: true
```
**Runtime**: 3-10 min | **Use case**: Regular active branch scanning

### Monthly Deep Scans
```yaml
deep_scan: true
per_repo_timeout: 10m
scan_parallel: 8
```
**Runtime**: 15-45 min | **Use case**: Full audit

### Large Organizations (1000+ repos)
```yaml
repos_per_shard: 100
shard_cap: 20
scan_parallel: 16
branch_strategy: main-only
scan_mode: recent
scan_lookback_days: 3
```
**Runtime**: 5-12 min | **Use case**: Fast large-org scanning

### Initial Baseline Scan
```yaml
deep_scan: true
per_repo_timeout: 15m
scan_parallel: 4
max_finding_log_lines: 1000
```
**Runtime**: 30-90 min | **Use case**: First comprehensive scan

## Default Test Configuration

When no `TRUFFLEHOG_ORGS` variable is set and no `org_names` input provided, the workflow scans public test repositories:

| Org | Test Repo | Contents |
|-----|-----------|----------|
| trufflesecurity | test_keys | AWS creds, SSH key, auth URI |
| gitleaks | fake-leaks | Various test secrets |

Default settings for testing:
- `repo_visibility: public` - scans only public repos
- `scan_results: all` - shows verified and unverified findings

## Workflow Summary

The workflow generates a detailed summary in GitHub Actions showing:

- **Findings table**: Total, Verified, Unverified counts
- **Detector breakdown**: Top 10 detector types with counts
- **Configuration**: All scan settings used
- **Per-org summaries**: Detailed findings for each organization

---

**See also**:
- [README.md](README.md) - Overview
- [SETUP.md](SETUP.md) - Setup guide
- [REMEDIATION.md](REMEDIATION.md) - Secrets remediation
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) - Performance tuning
