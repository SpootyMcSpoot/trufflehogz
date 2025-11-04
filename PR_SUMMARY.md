# Improve TruffleHog workflow: Fix scanning, prevent duplicates, and enhance issue quality

## Summary

This PR contains multiple improvements to the TruffleHog secret scanning workflow, addressing critical bugs and significantly improving the quality of created GitHub issues.

## Key Changes

### 1. Fixed TruffleHog Scanning Issues

**Problem**: TruffleHog 3.90.6 doesn't support the `--since` flag for date-based filtering, causing all scans to fail with 0 findings.

**Solution**:
- Removed unsupported `--since` date filtering flag
- TruffleHog now scans full repository history (appropriate for comprehensive security scanning)
- Added documentation explaining TruffleHog only supports `--since-commit` with commit references

**Impact**: Scans now successfully detect secrets instead of silently failing.

### 2. Fixed Duplicate Issue Creation

**Problem**: Same secrets were reported in multiple issues due to:
- Only checking open issues (closed issues were ignored)
- No unique identifier for findings across different days
- HTTP 422 errors when using Search API with fine-grained PATs

**Solution**:
- Added unique 8-character hash to issue titles based on findings (file, line, commit)
- Search both open AND closed issues to prevent re-notification
- Replaced GitHub Search API with Issues List API (compatible with fine-grained PATs)
- Extended lookback window to 90 days for hash-based deduplication

**Impact**:
- No more duplicate issues for the same unresolved secrets
- Works even if original issues are closed
- Prevents spam from repeated scanning

### 3. Enhanced Issue Quality and Security

**Problem**:
- GitHub automatically expands line anchor URLs into code previews, exposing secrets in issues
- Missing clickable links in issue tables
- No actionable remediation guidance
- Emojis in professional security issues

**Solution**:
- Removed line anchors from GitHub URLs to prevent secret exposure
- Added clickable `[View]` links in tables
- Restructured issue with clear sections and step-by-step remediation
- Added links to official GitHub documentation for removing sensitive data
- Removed all emojis for professional appearance

**Impact**: Secrets no longer exposed in GitHub issues, better remediation guidance.

### 4. Fixed API Compatibility Issues

**Problem**: HTTP 422 errors when checking for duplicate issues using Search API.

**Solution**:
- Replaced `/search/issues` with `/repos/{repo}/issues` endpoint
- Fine-grained PATs can access Issues API with "Issues: Read and write" permission
- Filter results in application code instead of query parameters

**Impact**: No more HTTP 422 errors, reliable duplicate detection.

### 5. Workflow Optimization

**Problem**: Workflow input limit (10 inputs max), redundant organization parameters.

**Solution**:
- Removed redundant `target_org` dropdown input
- Consolidated to single `org_names` text input
- Added early exit for empty findings to skip unnecessary processing
- Updated documentation

**Impact**: Cleaner workflow configuration within GitHub's limits.

### 6. Scheduled Scanning and Configuration Improvements

**Problem**: No automated scheduling, insufficient documentation for configuration.

**Solution**:
- Added cron schedule to run scans every Sunday at 6 PM UTC
- Added comprehensive "Required Secrets and Variables" section to README
- Added "Scheduled Runs" section with examples of common cron schedules
- Improved documentation on configuration priority order (input > variable)
- Added clear instructions for setting up repository secrets and variables

**Impact**: Automated weekly scanning, clearer setup instructions for users.

## Issue Title Format

Issues now include unique hash for better tracking:

**Before**: `[TruffleHog] Secrets scan report - 2025-11-04`
**After**: `[TruffleHog] Secrets scan report - 2025-11-04 (a3f5b8c2)`

The hash is deterministic - same findings = same hash, enabling duplicate detection across days.

## Example Issue

```markdown
## Secret Detection Alert

**Status**: 1 verified secret found in this repository
**Scan**: [View workflow run](...)

> **SECURITY NOTICE**: Do not paste secret values in this issue. All secrets below should be considered compromised.

### Findings

| Detector | File | Line | Link |
|---|---|---:|---|
| Github | .gitmodules | 3 | [View](...) |

---

## Remediation Steps

### Step 1: Rotate/Revoke Immediately
**Assume all secrets above are compromised.** Rotate or revoke them in your service provider:
- GitHub tokens: [Settings → Developer settings → Personal access tokens](...)
- AWS keys: Use AWS IAM Console
- Other services: Check your provider's credential management

### Step 2: Remove from Git History
**Recent commits** (last few commits):
```bash
git rebase -i HEAD~5  # Edit/drop commits containing secrets
```

**Older commits** (anywhere in history):
```bash
git filter-repo --replace-text <(echo 'YOUR_SECRET_HERE==>REDACTED')
```

### Step 3: Force Push & Notify Team
```bash
git push --force-with-lease
```
**WARNING**: Coordinate with your team before force-pushing to shared branches.

### Additional Resources
- [Complete remediation guide](...)
- [Removing sensitive data from a repository](...)
- [GitHub secret scanning documentation](...)
```

## Testing

- Verified scanning now detects secrets (previously returned 0 findings)
- Confirmed duplicate detection works with both open and closed issues
- Tested hash-based deduplication across multiple days
- Validated Issues API compatibility with fine-grained PATs
- Confirmed no secrets exposed in GitHub issue previews

## Breaking Changes

None. All changes are backward compatible.

## Deployment Notes

Existing open issues will remain unchanged. New scans will:
1. Detect secrets successfully (fixed scanning bug)
2. Create improved issue format with hash in title
3. Skip creating duplicates for both open and closed issues within 90 days

## Commits in This PR

1. Simplify issue template to actionable one-liners
2. Add early exit for empty findings in summarize-org job
3. Fix TruffleHog --since flag incompatibility
4. Improve issue summary and prevent secret exposure
5. Add GitHub docs link for purging secrets from git history
6. Fix HTTP 422 errors by replacing Search API with Issues API
7. Remove emojis from issue body
8. Fix datetime comparison error in search_recent_issues
9. Add unique findings hash to issue titles for better duplicate detection
10. Search both open and closed issues to prevent duplicates
11. Add comprehensive PR summary documentation
12. Add cron schedule and improve configuration documentation

---

**Files Changed**:
- `.github/workflows/trufflehog-org-scan.yml` - Fixed scanning flags, removed redundant input, added early exit, added cron schedule
- `trufflehog_scanner.py` - Fixed API calls, added hash generation, improved issue formatting
- `README.md` - Updated documentation with configuration guide and scheduled runs
- `PR_SUMMARY.md` - Comprehensive pull request summary
