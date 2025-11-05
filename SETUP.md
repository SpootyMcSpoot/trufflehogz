# Setup Guide

Complete setup instructions for TruffleHog Organization Scanner.

## Table of Contents
- [GitHub Personal Access Token (PAT)](#github-personal-access-token-pat)
- [Organization Configuration](#organization-configuration)
- [False Positive Filtering](#false-positive-filtering)

## GitHub Personal Access Token (PAT)

You need a GitHub token with appropriate permissions to scan repositories and optionally create issues. GitHub offers two token types:

### Fine-Grained Personal Access Token (Recommended)

Fine-grained tokens offer better security with granular, time-limited permissions scoped to specific repositories or organizations.

**To create a fine-grained PAT:**
1. Go to Settings → Developer settings → Personal access tokens → Fine-grained tokens
2. Click "Generate new token"
3. Set token name, expiration, and description
4. Under "Repository access", select:
   - **"All repositories"** (if scanning all orgs)
   - OR **"Only select repositories"** (choose specific repos)
5. Under "Permissions" → "Repository permissions", set:

**Minimum permissions (scanning only):**
| Permission | Access Level | Purpose |
|------------|-------------|---------|
| Contents | **Read** | Access repository code and history |
| Metadata | **Read** | Access basic repository information (automatic) |

**Additional permissions (for issue creation):**
| Permission | Access Level | Purpose |
|------------|-------------|---------|
| Issues | **Read and write** | Create and manage issues for findings |

**For organization scanning:**
6. Under "Permissions" → "Organization permissions", set:
   - **Members**: Read (access to enumerate org repos)

**Important notes for fine-grained tokens:**
- Tokens are scoped per-organization; you may need multiple tokens for multiple orgs
- Expiration is required (max 1 year); set a reminder to rotate
- More secure than classic PATs but requires more setup

### Classic Personal Access Token (Simpler)

Classic tokens are simpler but have broader permissions and no expiration requirement.

**To create a classic PAT:**
1. Go to Settings → Developer settings → Personal access tokens → Tokens (classic)
2. Click "Generate new token (classic)"
3. Set note (description) and expiration
4. Select scopes:

**Minimum permissions (scanning only):**
| Scope | Purpose |
|-------|---------|
| `repo` | Full control of private repositories (read access to code) |
| `read:org` | Read org membership and teams (enumerate repos) |

**Additional permissions (for issue creation):**
| Scope | Purpose |
|-------|---------|
| `repo` already includes issue creation | No additional scope needed |

**Important notes for classic tokens:**
- `repo` scope is broad (includes read/write for code, issues, PRs, etc.)
- Consider using fine-grained tokens for better security
- Set expiration and rotate regularly

### Comparison: Fine-Grained vs Classic

| Feature | Fine-Grained | Classic |
|---------|-------------|---------|
| Security | Better (granular permissions) | Broader (all-or-nothing scopes) |
| Setup Complexity | More complex | Simpler |
| Multi-Org Support | Requires token per org | Single token for all orgs |
| Expiration | Required (max 1 year) | Optional |
| Recommended For | Production, security-conscious | Quick setup, testing |

### Storing the Token

After creating either token type, store it as a repository secret:

1. Go to your repository Settings → Secrets and variables → Actions
2. Click "New repository secret"
3. Name: **`GH_PAT`**
4. Value: Paste your token
5. Click "Add secret"

### Troubleshooting Token Permissions

**401 Unauthorized errors:**
- Classic PAT: Ensure `repo` and `read:org` scopes are selected
- Fine-grained PAT: Ensure "Contents: Read" and "Members: Read" are granted
- Verify token hasn't expired
- Check that the user has access to the target organizations

**403 Forbidden errors:**
- User may not have access to the organization
- For private orgs, user must be a member
- Fine-grained token may be scoped to wrong repositories/orgs

**Issues not being created:**
- Classic PAT: `repo` scope already includes issue creation (no change needed)
- Fine-grained PAT: Ensure "Issues: Read and write" permission is granted
- Verify `open_issues: true` is set in workflow input
- Check that repository has issues enabled

## Organization Configuration

### Option A: Repository Variable (Recommended for defaults)
```
Repository Settings → Secrets and variables → Actions → Variables
Name: TRUFFLEHOG_ORGS
Value: org1,org2,org3
```

### Option B: Manual Text Input
Use the `org_names` input when clicking "Run workflow" (supports comma-separated list for single or multiple orgs)

**Priority Order**: `org_names` text input > `TRUFFLEHOG_ORGS` variable

## False Positive Filtering

Edit `.github/trufflehog/false_positives.txt`:
```regex
# Postgres DSN placeholders
postgres:\/\/username:password@hostname:\d+\/[^\/\s]+

# Obvious placeholders
\bexample(_|-)?(user|username|token|password|pass|key)\b
\bchangeme\b
\bplaceholder\b

# Documentation paths
file=.*\/docs\/
file=.*\/examples?\/
file=.*README(\.md|\.rst)?$
```

**How it works**: Patterns are matched against a composed string:
```
detector=<name> | verified=<bool> | repo=<owner/name> | file=<path> | redacted=<token>
```

---

**See also:**
- [Main README](README.md) - Overview and quick start
- [Configuration Guide](CONFIGURATION.md) - Detailed configuration examples
- [Remediation Guide](REMEDIATION.md) - How to remediate discovered secrets
- [Troubleshooting](TROUBLESHOOTING.md) - Performance tuning and troubleshooting
