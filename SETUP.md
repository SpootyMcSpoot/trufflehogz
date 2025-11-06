# Setup Guide

Complete setup for TruffleHog Organization Scanner.

## GitHub Personal Access Token

### Fine-Grained PAT (Recommended)

**Create**: Settings → Developer settings → Personal access tokens → Fine-grained tokens

**Permissions**:
- **Repository**: Contents (Read), Metadata (Read), Issues (Read/Write for issue creation)
- **Organization**: Members (Read)
- **Repository access**: All repositories or specific repos

**Notes**:
- Scoped per-organization (may need multiple tokens)
- Max 1 year expiration (required)
- More secure but complex setup

### Classic PAT (Simpler)

**Create**: Settings → Developer settings → Personal access tokens → Tokens (classic)

**Scopes**:
- `repo` (includes code read + issue creation)
- `read:org` (enumerate repos)

**Notes**:
- Broader permissions
- Single token for all orgs
- Optional expiration

### Comparison

| Feature | Fine-Grained | Classic |
|---------|-------------|---------|
| Security | Better (granular) | Broader |
| Setup | Complex | Simple |
| Multi-Org | Token per org | Single token |
| Expiration | Required (1 year max) | Optional |

### Store Token

Repository Settings → Secrets and variables → Actions → Secrets

1. Click "New repository secret"
2. Name: `GH_PAT`
3. Value: Paste token
4. Click "Add secret"

### Troubleshooting

**401 Unauthorized**:
- Classic: Ensure `repo` + `read:org` scopes
- Fine-grained: Ensure Contents (Read) + Members (Read)
- Check token expiration
- Verify org access

**403 Forbidden**:
- User must be org member
- Fine-grained: Check repo/org scope

**Issues not created**:
- Classic: `repo` includes issue creation
- Fine-grained: Add Issues (Read/Write)
- Verify `open_issues: true`

## Organization Configuration

### Option A: Repository Variable (Recommended)

Settings → Secrets and variables → Actions → Variables

- Name: `TRUFFLEHOG_ORGS`
- Value: `org1,org2,org3`

### Option B: Manual Input

Use `org_names` input when running workflow

**Priority**: `org_names` input > `TRUFFLEHOG_ORGS` variable

## False Positive Filtering

Edit `.github/trufflehog/false_positives.txt`:

```regex
# Placeholders
postgres:\/\/username:password@hostname:\d+\/[^\/\s]+
\bexample(_|-)?(user|username|token|password|pass|key)\b
\bchangeme\b
\bplaceholder\b

# Documentation
file=.*\/docs\/
file=.*\/examples?\/
file=.*README(\.md|\.rst)?$
```

**Pattern matching**: `detector=<name> | verified=<bool> | repo=<owner/name> | file=<path> | redacted=<token>`

---

**See also**:
- [README.md](README.md) - Overview
- [CONFIGURATION.md](CONFIGURATION.md) - Examples
- [REMEDIATION.md](REMEDIATION.md) - Secrets remediation
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) - Performance tuning
