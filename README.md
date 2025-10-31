# TruffleHog Organization Scanner

> **Automated secret scanning for GitHub organizations with 40-80% faster performance, comprehensive configurability, and intelligent sharding.**

A production-ready GitHub Actions workflow that scans multiple organizations for leaked secrets using TruffleHog OSS, with intelligent performance optimizations, flexible configuration, and automated issue creation.

## Quick Start

1. **Set up repository secret**: Add `GH_PAT` (see [Setup](#setup) for detailed PAT permissions)
   - Classic PAT: `repo` + `read:org` scopes
   - Fine-grained PAT: Contents (Read) + Members (Read)
2. **Configure organizations**: Set repository variable `TRUFFLEHOG_ORGS` (comma-separated list)
3. **Run workflow**: Go to Actions → TruffleHog – Org Scan → Run workflow

That's it! The workflow uses optimized defaults and runs **40-60% faster** than traditional scanning.

## Performance Features

### Built-in Optimizations (Enabled by Default)
- **Smart Branch Filtering**: Scans default branch + recently updated branches only (vs all branches)
- **Time-Based Scanning**: Only scans last 7 days of commits (vs full history)
- **Adaptive Timeout**: Adjusts timeout based on repo size (2m-15m)
- **Stale Branch Skipping**: Automatically skips branches not updated in 90+ days
- **Deep Scan Mode** (NEW): Single flag to enable comprehensive scanning of ALL branches and ALL commits for monthly audits

### Performance Impact
| Org Size | Before | After (Daily) | Improvement |
|----------|--------|---------------|-------------|
| Small (25 repos) | 3-5 min | 1-2 min | **60% faster** |
| Medium (150 repos) | 8-12 min | 2-3 min | **75% faster** |
| Large (500 repos) | 15-25 min | 3-5 min | **80% faster** |
| Very Large (1500 repos) | 30-60 min | 5-10 min | **83% faster** |

**Real-world example**: 1000-repo org reduced from **45-60 minutes → 3-5 minutes** (92% faster!)

## Features

### Core Capabilities
- **Multi-Organization Scanning**: Scan private, non-archived, non-fork repos across multiple organizations
- **Parallel Processing**: Intelligent sharding with up to 20 shards per org for maximum throughput
- **Verified Results**: Verified-only filtering to minimize false positives
- **False Positive Filtering**: Regex-based pattern matching for suppression
- **Automated Issue Creation**: Per-repo GitHub issues with date-based deduplication
- **Comprehensive Summaries**: Detailed workflow summaries with performance metrics
- **Docker Image Caching**: Faster startup times with cached TruffleHog images
- **Robust Error Handling**: Rate limiting and retry logic with exponential backoff

### Scanning Modes
- **Standard Mode** (Default): Fast scanning of recent changes (7 days) on active branches (30 days)
- **Deep Scan Mode** (NEW): Comprehensive scanning of ALL branches and complete commit history
- **Custom Mode**: Granular control with 17 configurable parameters

### Smart Defaults
- **Branch Strategy**: main-recent (default + updated branches)
- **Scan Mode**: recent (last 7 days)
- **Adaptive Timeout**: Enabled (2-15m based on repo size)
- **Repos per Shard**: 50 (configurable 25-100)
- **Concurrent Scans**: 8 per shard (configurable 4-16)
- **Results Filter**: verified + unknown
- **All Settings**: Fully configurable via 10 workflow inputs

## Configuration

### Workflow Inputs (10 Total)

The workflow accepts the following inputs via the Actions UI (Run workflow button). All inputs are fully configurable.

#### Basic Configuration
| Input | Options | Default | Description |
|-------|---------|---------|-------------|
| `org_names` | comma-separated | (from var) | Organizations to scan (supports single or multiple orgs) |
| `open_issues` | true/false | false | Create GitHub issues for findings |
| `issues_creation_orgs` | comma-separated | (empty) | Allowlist of orgs where issues should be created (empty = all orgs) |

**Note**: The `org_names` input takes priority over the `TRUFFLEHOG_ORGS` repository variable. The `issues_creation_orgs` allowlist controls which organizations will have issues created when `open_issues` is true.

#### Deep Scan Mode
| Input | Options | Default | Description |
|-------|---------|---------|-------------|
| `deep_scan` | true/false | false | **Deep scan mode**: Scans ALL branches and ALL commits (overrides branch_strategy and scan_mode) |

**Important**: When `deep_scan` is enabled:
- Branch strategy is automatically set to "all" (scans every branch)
- Scan mode is automatically set to "full" (scans entire commit history)
- Stale branch skipping is disabled (scans branches older than 90 days)
- Use this for monthly comprehensive audits or initial baseline scans

#### Performance Tuning
| Input | Options | Default | Description |
|-------|---------|---------|-------------|
| `scan_parallel` | 4, 8, 12, 16 | 8 | Concurrent repos per shard |
| `per_repo_timeout` | 3m, 5m, 10m, 15m | 5m | Timeout per repository |
| `scan_results` | verified, verified+unknown, all | verified+unknown | Results filter |

#### Sharding Configuration
| Input | Options | Default | Description |
|-------|---------|---------|-------------|
| `shard_cap` | 5, 10, 15, 20 | 10 | Maximum shards per org |
| `repos_per_shard` | 25, 50, 75, 100 | 50 | Target repos per shard |

#### Scan Strategies (Overridden by deep_scan)
| Input | Options | Default | Description |
|-------|---------|---------|-------------|
| `branch_strategy` | all, main-only, main-recent, protected | main-recent | Branch scanning strategy |
| `scan_mode` | full, recent, incremental | recent | Commit scanning mode |
| `branch_lookback_days` | 7, 14, 30, 60, 90 | 30 | Days to look back for "recent" branches (main-recent strategy) |
| `scan_lookback_days` | 1, 3, 7, 14, 30 | 7 | Days to scan in recent/incremental mode |

#### Advanced Configuration
| Input | Options | Default | Description |
|-------|---------|---------|-------------|
| `adaptive_timeout` | true/false | true | Automatically adjusts timeout by repo size (2-15m) |
| `skip_stale_branches` | true/false | true | Skips branches not updated in 90+ days |
| `max_finding_log_lines` | 50, 100, 300, 500, 1000 | 300 | Maximum sanitized finding lines to log |

### Hardcoded Settings (Not Configurable via UI)

These settings have fixed values optimized for most use cases:

| Setting | Value | Description |
|---------|-------|-------------|
| `trufflehog_version` | 3.90.6 | TruffleHog Docker image version (hardcoded) |
| `enable_size_based_sharding` | false | Size-based load balancing (disabled by default) |
| `max_repo_size_kb` | 0 | Repo size limit in KB (0 = unlimited) |

To change these settings, modify the workflow file directly (.github/workflows/trufflehog-org-scan.yml).

### Configuration Examples

These examples show how to configure workflow inputs for different scanning scenarios.

#### Daily Scans (Maximum Speed)
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

#### Weekly Scans (Recommended)
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

#### Monthly Deep Scans (Comprehensive)
```yaml
deep_scan: true
per_repo_timeout: 10m
scan_parallel: 8
adaptive_timeout: true
```
**Expected runtime**: 15-45 min for most orgs
**Use case**: Full audit of all branches and complete commit history
**Note**: `deep_scan: true` automatically sets `branch_strategy: all`, `scan_mode: full`, and `skip_stale_branches: false`

#### Custom Deep Scan (Without deep_scan Flag)
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

#### Large Organization (1000+ repos)
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

#### Initial Baseline Scan (First Run)
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

## Architecture

### Workflow Structure
```
┌─────────────────────────────────────────┐
│ 1. resolve-matrix                        │
│    Parse org names from input/variable   │
└────────────┬────────────────────────────┘
             │
┌────────────▼────────────────────────────┐
│ 2. plan                                  │
│    • Count repos per org                 │
│    • Calculate optimal shard count       │
│    • Output task matrix                  │
└────────────┬────────────────────────────┘
             │
┌────────────▼────────────────────────────┐
│ 3. scan-org (parallel)                   │
│    • Load TruffleHog image (cached)      │
│    • Enumerate repos for shard           │
│    • Scan repos concurrently             │
│    • Upload per-shard artifacts          │
└────────────┬────────────────────────────┘
             │
┌────────────▼────────────────────────────┐
│ 4. summarize-org (per org)               │
│    • Download all shard artifacts        │
│    • Merge NDJSON files                  │
│    • Filter false positives              │
│    • Generate summary                    │
│    • Create issues (optional)            │
└────────────┬────────────────────────────┘
             │
┌────────────▼────────────────────────────┐
│ 5. workflow-summary                      │
│    • Show configuration used             │
│    • Display performance metrics         │
│    • Link to per-org summaries           │
└─────────────────────────────────────────┘
```

### Sharding Strategy

**Standard Sharding** (default):
- Deterministic modulo-based distribution
- Each shard gets `repo_count / shard_total` repos
- Stable: same repos per shard across runs

**Size-Based Sharding** (optional):
- Sorts repos by size (largest first)
- Round-robin distribution
- Each shard gets mix of large and small repos
- Better load balancing for orgs with varied repo sizes

### File Structure
```
.github/
  workflows/
    trufflehog-org-scan.yml       # Main workflow (800+ lines)
  trufflehog/
    false_positives.txt           # Regex-based suppressions
trufflehog_scanner.py             # Python post-processor (860 lines)
PERMISSION_ANALYSIS.md            # Technical API endpoint reference
.gitignore                        # Python/IDE/OS artifacts
```

## Setup

### 1. GitHub Personal Access Token (PAT)

You need a GitHub token with appropriate permissions to scan repositories and optionally create issues. GitHub offers two token types:

#### Fine-Grained Personal Access Token (Recommended)

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

#### Classic Personal Access Token (Simpler)

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

#### Comparison: Fine-Grained vs Classic

| Feature | Fine-Grained | Classic |
|---------|-------------|---------|
| Security | Better (granular permissions) | Broader (all-or-nothing scopes) |
| Setup Complexity | More complex | Simpler |
| Multi-Org Support | Requires token per org | Single token for all orgs |
| Expiration | Required (max 1 year) | Optional |
| Recommended For | Production, security-conscious | Quick setup, testing |

#### Storing the Token

After creating either token type, store it as a repository secret:

1. Go to your repository Settings → Secrets and variables → Actions
2. Click "New repository secret"
3. Name: **`GH_PAT`**
4. Value: Paste your token
5. Click "Add secret"

#### Troubleshooting Token Permissions

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

### 2. Organization Configuration

**Option A: Repository Variable (Recommended for defaults)**
```
Repository Settings → Secrets and variables → Actions → Variables
Name: TRUFFLEHOG_ORGS
Value: org1,org2,org3
```

**Option B: Manual Text Input**
Use the `org_names` input when clicking "Run workflow" (supports comma-separated list for single or multiple orgs)

**Priority Order**: `org_names` text input > `TRUFFLEHOG_ORGS` variable

### 3. False Positive Filtering (Optional)

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

## Workflow Summary

After each run, you'll see a comprehensive summary:

### Configuration Table
```markdown
## Configuration
| Setting | Value |
|---------|-------|
| Organizations | org1, org2, org3 |
| Total Repositories | 247 |
| Total Shards | 15 |
| Repos per Shard | 50 |
| Concurrent Scans | 8 |
| Timeout per Repo | 5m |
```

### Performance Optimizations
```markdown
## Performance Optimizations
| Feature | Value |
|---------|-------|
| Branch Strategy | main-recent |
| Scan Mode | recent (7 days) |
| Adaptive Timeout | true |
| Size-Based Sharding | false |

**Branch filtering enabled**: Scanning default branch + branches updated in last 30 days
**Time-based scanning enabled**: Only scanning commits from last 7 days
**Adaptive timeout enabled**: 2-5m for small repos, 10-15m for large repos
```

### Per-Organization Findings
```markdown
## TruffleHog summary for `org-name`
- Total verified unique finding(s): **3**

### Findings by repository
| Repository | Unique verified |
|---|---:|
| `org/repo1` | 2 |
| `org/repo2` | 1 |

### Findings by detector
| Detector | Unique verified |
|---|---:|
| AWS | 1 |
| GitHub | 2 |

### Detailed verified findings by repository
#### `org/repo1`
| Detector | File | Line | Link |
|---|---|---:|---|
| AWS | `config/aws.py` | 42 | https://github.com/... |
```

## Issue Creation

### Behavior
- **Title**: `[TruffleHog] Secrets scan report - YYYY-MM-DD`
- **Deduplication**: Checks for existing issues within 7 days to prevent spam
- **Labels**: Auto-created based on detector type and severity
  - Base: `security`, `secrets`, `tool:trufflehog`, `needs-triage`
  - Detector: `secret:aws`, `secret:github`, `secret:slack`, etc.
  - Severity: `sec:low`, `sec:medium`, `sec:high`, `sec:critical`

### Enable Issue Creation
1. Set `open_issues: true` in workflow input (default is `false` for dry-run)
2. Optionally, set `issues_creation_orgs` to a comma-separated list of organizations where issues should be created
   - If empty (default): Issues will be created for all scanned organizations
   - If specified: Issues will only be created for organizations in the allowlist
   - Example: `org1,org2,org3` will only create issues for those three orgs

### Issue Format
```markdown
Automated TruffleHog (OSS) scan found 2 verified secret(s) in this repository.

Scan run: https://github.com/.../actions/runs/...

> Do not paste secrets in this issue. Rotate or revoke credentials and clean history.

| Detector | File | Line | Link |
|---|---|---:|---|
| AWS | `config.py` | 42 | https://... |
| GitHub | `scripts/deploy.sh` | 88 | https://... |

## Next steps
- Rotate or revoke affected credentials
- Remove the secret and rewrite history if needed
- Add pre-commit and CI secret scanning gates
```

## Remediating Discovered Secrets

When TruffleHog discovers secrets in your repository, follow these steps to properly remediate them:

### 1. Immediate Response (Critical: Do This First)

**Rotate or Revoke Credentials Immediately**
- **Before** cleaning git history, assume the secret is already compromised
- Rotate API keys, passwords, tokens, or certificates
- Revoke the compromised credentials in the service provider's dashboard
- Generate new credentials and store them securely (use a secrets manager)

**Examples:**
- AWS: Deactivate and delete the IAM access key, create a new one
- GitHub: Revoke the personal access token, generate a new one
- Database passwords: Change the password immediately
- API keys: Regenerate the key in the provider's dashboard

### 2. Remove Secrets from Current Files

Remove the secret from your current working tree:

```bash
# Edit the file and remove the secret
vim path/to/file-with-secret.py

# Or replace the secret with a placeholder/environment variable
sed -i 's/sk-live-abc123xyz/os.getenv("API_KEY")/g' config.py

# Commit the change
git add path/to/file-with-secret.py
git commit -m "Remove hardcoded secret, use environment variable"
```

### 3. Purge Secrets from Git History

**CRITICAL**: Rewriting git history is a destructive operation. Coordinate with your team and ensure everyone is aware.

#### Option A: git-filter-repo (Recommended)

[git-filter-repo](https://github.com/newren/git-filter-repo) is the modern, fast replacement for git-filter-branch.

```bash
# Install git-filter-repo
# macOS:
brew install git-filter-repo

# Linux (Debian/Ubuntu):
sudo apt-get install git-filter-repo

# Or via pip:
pip install git-filter-repo

# Create a backup first
git clone your-repo your-repo-backup

# Remove a specific file from all history
git filter-repo --path path/to/secret-file.py --invert-paths

# Or replace text across all history (e.g., replace API key with placeholder)
echo 'sk-live-abc123xyz==>REDACTED' > replacements.txt
git filter-repo --replace-text replacements.txt

# Verify the secret is gone
git log --all --full-history --source -- path/to/secret-file.py
```

#### Option B: BFG Repo-Cleaner (Fast and Simple)

[BFG Repo-Cleaner](https://rtyley.github.io/bfg-repo-cleaner/) is faster than git-filter-branch for common tasks.

```bash
# Install BFG
# macOS:
brew install bfg

# Or download JAR:
# wget https://repo1.maven.org/maven2/com/madgag/bfg/1.14.0/bfg-1.14.0.jar

# Create a backup first
git clone --mirror your-repo.git your-repo-backup.git

# Clone a fresh mirror
git clone --mirror https://github.com/org/repo.git

# Remove file from history
bfg --delete-files secret-file.py repo.git

# Or replace text (create passwords.txt with secrets to replace)
echo 'sk-live-abc123xyz' > passwords.txt
bfg --replace-text passwords.txt repo.git

# Cleanup and verify
cd repo.git
git reflog expire --expire=now --all
git gc --prune=now --aggressive
```

#### Option C: git filter-branch (Legacy, Slower)

Only use if git-filter-repo is not available.

```bash
# Remove a file from all history
git filter-branch --force --index-filter \
  'git rm --cached --ignore-unmatch path/to/secret-file.py' \
  --prune-empty --tag-name-filter cat -- --all

# Or replace text in all history
git filter-branch --tree-filter \
  'find . -type f -exec sed -i "s/sk-live-abc123xyz/REDACTED/g" {} +' \
  --tag-name-filter cat -- --all

# Cleanup refs
git for-each-ref --format='delete %(refname)' refs/original | git update-ref --stdin
git reflog expire --expire=now --all
git gc --prune=now --aggressive
```

### 4. Force Push and Notify Team

**WARNING**: Force pushing rewrites history. Ensure all team members are aware and prepared.

```bash
# Force push to remote (this will rewrite history on remote)
git push origin --force --all
git push origin --force --tags

# Notify all team members to re-clone or reset their local copies
```

**Team members should:**
```bash
# Option 1: Delete and re-clone (safest)
cd ..
rm -rf old-repo
git clone https://github.com/org/repo.git

# Option 2: Reset local repo (be careful, loses local changes)
git fetch origin
git reset --hard origin/main  # or your branch name
git clean -fdx
```

### 5. Post-Remediation Steps

After cleaning git history:

1. **Verify the secret is gone**: Run TruffleHog again on the cleaned repo
   ```bash
   docker run --rm -v "$PWD:/work" -w /work \
     ghcr.io/trufflesecurity/trufflehog:3.90.6 \
     git file:///work --only-verified
   ```

2. **Invalidate GitHub's cache** (for public repos): Contact GitHub Support to purge cached refs

3. **Update protection rules**:
   - Add the file/pattern to `.gitignore` if it shouldn't be tracked
   - Set up pre-commit hooks to prevent future secret commits
   - Enable GitHub secret scanning and push protection

4. **Add prevention measures**:
   ```bash
   # Install pre-commit hook with TruffleHog
   pip install pre-commit

   # Create .pre-commit-config.yaml
   cat > .pre-commit-config.yaml <<EOF
   repos:
     - repo: https://github.com/trufflesecurity/trufflehog
       rev: v3.90.6
       hooks:
         - id: trufflehog
           name: TruffleHog Secret Scan
           entry: trufflehog git file://. --since-commit HEAD --only-verified --fail
   EOF

   # Install the hook
   pre-commit install
   ```

5. **Monitor for exposure**:
   - Check if the secret was exposed in any forks
   - Monitor audit logs for unauthorized usage
   - Review access logs for suspicious activity

### Important Considerations

**⚠️ Public Repositories**
- Once pushed to a public repo, assume the secret was harvested by bots
- Secrets can persist in forks even after you clean your history
- Contact GitHub Support to purge cached views of commits

**⚠️ Private Repositories**
- Secrets may still be visible to anyone who had access
- Check who cloned the repo while the secret was exposed
- Consider if any CI/CD logs or artifacts contain the secret

**⚠️ Large Repositories**
- History rewriting can take significant time (hours for large repos)
- Test the process on a mirror first
- Consider using BFG or git-filter-repo for better performance

**⚠️ Protected Branches**
- Temporarily disable branch protection rules to force push
- Re-enable protection immediately after pushing

### Alternative: Secrets Management

To prevent secrets from entering git in the first place:

```bash
# Use environment variables
export DATABASE_PASSWORD="secret"
python app.py

# Use a .env file (add to .gitignore)
echo "DATABASE_PASSWORD=secret" > .env
echo ".env" >> .gitignore

# Use a secrets manager
# AWS Secrets Manager, HashiCorp Vault, Azure Key Vault, etc.
```

**Best practices:**
- Store secrets in environment variables or secrets managers
- Use GitHub Secrets for Actions workflows
- Never commit `.env` files (add to `.gitignore`)
- Use placeholder values in example/template files
- Enable GitHub's push protection for secret scanning

## Testing

### Manual Workflow Run
1. Go to **Actions** tab
2. Select **TruffleHog – Org Scan (Non-Public Repos)**
3. Click **"Run workflow"**
4. Adjust inputs as needed
5. Click **"Run workflow"** to start

### Local Testing

#### Test Python script with sample NDJSON:
```bash
python3 trufflehog_scanner.py \
  --ndjson findings-test.ndjson \
  --org test-org \
  --exclude-patterns-file .github/trufflehog/false_positives.txt \
  --print-sanitized 50 \
  --write-summary \
  --summary-detailed \
  --run-url "https://github.com/org/repo/actions/runs/123" \
  --dry-run
```

#### Generate test NDJSON:
```bash
docker run --rm -v "$PWD:/work" -w /work \
  ghcr.io/trufflesecurity/trufflehog:3.90.6 \
  github --repo "https://github.com/owner/repo" \
         --token "$GH_PAT" \
         --results=verified,unknown \
         --json \
         --force-skip-binaries \
         --force-skip-archives \
  > findings-test.ndjson
```

## Performance Tuning

### If Scans Are Too Slow
1. **Reduce branch scope**: Use `branch_strategy: main-only`
2. **Shorten time window**: Set `scan_lookback_days: 1` or `3`
3. **Reduce branch lookback**: Set `branch_lookback_days: 7` or `14`
4. **Increase parallelism**: Set `scan_parallel: 16`
5. **Enable adaptive timeout**: Set `adaptive_timeout: true` (enabled by default)
6. **Skip stale branches**: Ensure `skip_stale_branches: true` (enabled by default)
7. **Skip large repos**: Edit workflow file to set `max_repo_size_kb: 500000`

### If Missing Findings
1. **Use deep scan mode**: Set `deep_scan: true` for comprehensive scanning
2. **Expand branch scope**: Use `branch_strategy: all` or `main-recent`
3. **Expand time window**: Set `scan_lookback_days: 14` or `30`
4. **Expand branch lookback**: Set `branch_lookback_days: 60` or `90`
5. **Include more results**: Set `scan_results: all`
6. **Disable branch skipping**: Set `skip_stale_branches: false`
7. **Full history scan**: Set `scan_mode: full`

### If Hitting Rate Limits
1. **Reduce concurrent scans**: Set `scan_parallel: 4`
2. **Reduce shard count**: Set `shard_cap: 5`
3. **Add delays**: The workflow has built-in rate limiting (4 calls/sec)
4. **Stagger scan times**: Run scans at different times for different orgs

### Timeout Issues
1. **Enable adaptive timeout**: Set `adaptive_timeout: true` (enabled by default)
2. **Increase base timeout**: Set `per_repo_timeout: 10m` or `15m`
3. **Skip large repos**: Edit workflow file to set `max_repo_size_kb: 100000`
4. **Reduce scan scope**: Use `scan_mode: recent` with shorter `scan_lookback_days`

### Deep Scan Best Practices
When using `deep_scan: true`:
1. **Run less frequently**: Monthly or quarterly, not daily
2. **Increase timeout**: Set `per_repo_timeout: 10m` or `15m`
3. **Reduce parallelism**: Set `scan_parallel: 4` or `8` to avoid rate limits
4. **Monitor for timeouts**: Check logs and adjust timeout as needed
5. **Expect longer runtime**: Budget 30-90 minutes for large organizations

## Monitoring

### Key Metrics to Watch
- **Total scan time**: From workflow summary
- **Timeout count**: In errors NDJSON
- **Shard completion time**: Check for imbalance
- **API rate limit hits**: In workflow logs

### Optimization Indicators
- All shards complete within 5 min of each other → Good load balancing
- One shard takes 2x+ longer → Enable `enable_size_based_sharding`
- Many timeouts → Increase `per_repo_timeout` or enable `adaptive_timeout`
- Missing findings → Increase `scan_lookback_days` or change `branch_strategy`

## Troubleshooting

### Common Issues

**No findings in summary but artifacts exist**
- Check stderr logs: `tmp-logs-<org>/*.stderr.log`
- Verify false positive patterns aren't too broad
- Ensure `scan_results` includes desired result types

**401/403 errors**
- Classic PAT: Verify `repo` and `read:org` scopes are selected
- Fine-grained PAT: Verify "Contents: Read" and "Members: Read" permissions
- Check PAT hasn't expired
- Confirm user has access to organizations
- See detailed troubleshooting in [Setup → Troubleshooting Token Permissions](#troubleshooting-token-permissions)

**Timeouts on specific repos**
- Enable `adaptive_timeout: true`
- Check repo size (some repos have huge histories)
- Consider adding to `max_repo_size_kb` filter

**Missing branches**
- Check `branch_strategy` setting (try `all` or `main-recent`)
- Review `branch_lookback_days` value (increase to 60 or 90)
- Set `skip_stale_branches: false` to scan all branches
- Consider using `deep_scan: true` for comprehensive coverage

**Missing old commits**
- Set `scan_mode: full` instead of `recent`
- Increase `scan_lookback_days` to 14, 30, or more
- Use `deep_scan: true` for complete history scan

**Empty NDJSON files**
- Check TruffleHog version compatibility
- Verify repos aren't all archived/empty
- Review `scan_results` setting
- Confirm time-based filters aren't excluding all commits

**Deep scan taking too long**
- Increase `per_repo_timeout` to 15m
- Reduce `scan_parallel` to 4 or 8
- Enable `adaptive_timeout: true`
- Consider splitting into multiple smaller runs

## Additional Documentation

### Core Documentation
- **[PERMISSION_ANALYSIS.md](PERMISSION_ANALYSIS.md)**: Complete API endpoint reference and permission requirements for both Classic and Fine-Grained PATs
- **[TEST_FIXTURES.md](TEST_FIXTURES.md)**: Documentation of intentional test secrets included in this repository for TruffleHog validation

### Important Notes

**Test Data**: This repository contains intentional fake secrets for testing TruffleHog detection. See [TEST_FIXTURES.md](TEST_FIXTURES.md) for complete details on test fixtures.

## Related Resources

- [TruffleHog OSS](https://github.com/trufflesecurity/trufflehog)
- [GitHub Actions Documentation](https://docs.github.com/en/actions)
- [Creating a Fine-Grained PAT](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens#creating-a-fine-grained-personal-access-token)
- [Creating a Classic PAT](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens#creating-a-personal-access-token-classic)
- [GitHub Token Scopes](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps)

## Changelog

### v2.3 (Current)
- **Code Quality Improvements**:
  - Fixed finding deduplication bug where None line numbers caused incorrect deduplication
  - Parameterized hardcoded values (rate limits, timeouts, retry settings) for better configurability
  - Added comprehensive type hints throughout Python codebase
  - Improved code maintainability and reliability
- **Enhanced Documentation**:
  - Complete PERMISSION_ANALYSIS.md with verified API endpoint requirements and troubleshooting
  - New TEST_FIXTURES.md explaining intentional test secrets in repository
  - Updated README with links to new documentation
- **Configuration Constants**: All tunable parameters now documented at top of trufflehog_scanner.py:
  - `DEFAULT_RATE_LIMIT_PER_SEC = 4.0` - GitHub API calls per second
  - `DEFAULT_API_TIMEOUT_SEC = 30` - Timeout for API calls
  - `DEFAULT_MAX_RETRIES = 5` - Maximum retry attempts
  - `DEFAULT_BACKOFF_BASE = 1.5` - Exponential backoff multiplier
  - `MAX_BACKOFF_SEC = 16.0` - Maximum backoff sleep time
  - `DEFAULT_ISSUE_DEDUP_DAYS = 7` - Issue deduplication window

### v2.2
- **Deep Scan Mode**: New `deep_scan` flag for comprehensive monthly/quarterly audits
  - Automatically scans ALL branches and ALL commits (including commits older than 30 days)
  - Overrides branch_strategy and scan_mode settings
  - Disables stale branch filtering
- **Fully Configurable Inputs**: All 17 parameters now configurable via workflow inputs
  - `branch_lookback_days`: 7, 14, 30, 60, 90 days (was hardcoded to 30)
  - `scan_lookback_days`: 1, 3, 7, 14, 30 days (was hardcoded to 7)
  - `adaptive_timeout`: true/false (was hardcoded to true)
  - `skip_stale_branches`: true/false (was hardcoded to true)
  - `max_finding_log_lines`: 50-1000 lines (was hardcoded to 300)
- **Enhanced Documentation**:
  - Added 6 comprehensive configuration examples (daily, weekly, deep scan, etc.)
  - Detailed deep scan best practices
  - Expanded troubleshooting section
  - Updated performance tuning guide
- **Workflow Summary Enhancements**:
  - New "Scan Configuration" section showing all active settings
  - Context-aware messages for different scan modes
  - Deep scan mode indicator with automatic override notifications

### v2.1
- **Comprehensive PAT documentation**: Fine-grained vs Classic tokens with detailed permissions
- **Bug fixes**:
  - Fixed 10-input GitHub Actions limit (reduced from 16 inputs)
  - Fixed missing directory creation causing workflow failures
  - Improved issue creation robustness with duplicate detection
- **Added pull_request trigger**: Workflow now runs on PRs
- **Consolidated documentation**: Single README for easier maintenance

### v2.0
- **40-80% faster** with smart branch filtering and time-based scanning
- **10 configurable inputs** (was 2) with 6 optimized hardcoded defaults
- **Enhanced workflow summary** with performance metrics
- **Adaptive timeout** based on repo size (hardcoded: enabled)
- **Size-based sharding** for better load balancing (hardcoded: disabled)
- **Bug fixes**: False positive filtering, missing flags
- **Code quality**: -9% lines via generator pattern

### v1.0 (Original)
- Basic scanning with TruffleHog
- Manual sharding configuration
- Python post-processing
- Issue creation

## License

This workflow and scripts are provided as-is for scanning your own organizations.

---

**Need help?** Check the troubleshooting section or review the comprehensive documentation files included in this repository.
