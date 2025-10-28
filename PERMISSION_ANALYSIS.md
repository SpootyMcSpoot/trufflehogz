# Fine-Grained PAT Permission Analysis

This document provides a comprehensive analysis of GitHub API endpoints used by TruffleHogz and the required permissions for both Classic and Fine-Grained Personal Access Tokens.

## Overview

TruffleHogz consists of two main components:
1. **GitHub Actions Workflow** (Bash script) - Enumerates and scans repositories
2. **Python Post-Processor** - Creates issues, labels, and summaries

## API Endpoints Used

### Workflow (Bash Script)

| Endpoint | Method | Purpose | Required Permission |
|----------|--------|---------|-------------------|
| `/user/memberships/orgs/{org}` | GET | Verify user is member of organization | Organization: Members (Read) |
| `/orgs/{org}/repos` | GET | List all organization repositories | Organization: Members (Read) + Repository: Contents (Read) |
| `/repos/{owner}/{repo}` | GET | Get repository metadata (size, default branch, etc.) | Repository: Metadata (Read) - automatic |
| `/repos/{owner}/{repo}/branches` | GET | List repository branches | Repository: Contents (Read) |
| Git clone via HTTPS | GET | Clone repository content for scanning | Repository: Contents (Read) |

### Python Script

| Endpoint | Method | Purpose | Required Permission |
|----------|--------|---------|-------------------|
| `/repos/{repo}` | GET | Get repository metadata (archived status, issues enabled) | Repository: Metadata (Read) - automatic |
| `/search/issues` | GET | Search for existing issues (deduplication) | Repository: Issues (Read) |
| `/repos/{repo}/labels` | GET | List repository labels | Repository: Issues (Read) |
| `/repos/{repo}/labels` | POST | Create missing labels | Repository: Issues (Write) |
| `/repos/{repo}/issues` | POST | Create new issue for findings | Repository: Issues (Write) |

## Required Permissions

### Fine-Grained Personal Access Token (Recommended)

Fine-grained tokens provide granular, time-limited permissions scoped to specific organizations.

#### Scanning Only (No Issue Creation)

**Repository Permissions:**
- **Contents**: Read - Access code, branches, and commit history
- **Metadata**: Read - Automatically granted (repository info)

**Organization Permissions:**
- **Members**: Read - Enumerate organization repositories and verify membership

#### Scanning + Issue Creation

**Repository Permissions:**
- **Contents**: Read - Access code, branches, and commit history
- **Issues**: Read and write - Create issues and labels for findings
- **Metadata**: Read - Automatically granted (repository info)

**Organization Permissions:**
- **Members**: Read - Enumerate organization repositories and verify membership

#### Important Notes for Fine-Grained Tokens

1. **Organization Scope**: Fine-grained tokens are scoped per organization
   - For multiple organizations, you need multiple tokens OR
   - Use a Classic PAT (simpler but less secure)

2. **Repository Access**: When creating the token, select:
   - "All repositories" for the organization, OR
   - "Only select repositories" (must include all repos you want to scan)

3. **Expiration**: Fine-grained tokens require expiration (max 1 year)
   - Set calendar reminder to rotate before expiry
   - Test token rotation procedure in advance

4. **Resource Owner**: Must be the organization for org-level scanning
   - Personal account tokens can only access repos where you're a collaborator

### Classic Personal Access Token (Simpler)

Classic tokens use broader scopes but work across all organizations.

#### Scanning Only (No Issue Creation)

**Scopes:**
- `repo` (or `public_repo` for public repos only) - Full repository access
- `read:org` - Read organization membership and repositories

**Note**: `repo` scope includes read access to code, issues, PRs, and more. There's no granular "read-only code" scope in Classic PATs.

#### Scanning + Issue Creation

**Scopes:**
- `repo` - Full repository access (includes issue creation)
- `read:org` - Read organization membership

**Note**: The `repo` scope already includes issue creation, so no additional scope is needed.

#### Important Notes for Classic Tokens

1. **Broad Permissions**: `repo` grants full read/write access to repositories
   - This is more permissive than necessary for scanning
   - Consider using fine-grained tokens for production deployments

2. **Cross-Organization**: Single token works for all organizations where you have access
   - Simpler for multi-org setups
   - Higher security risk if token is compromised

3. **Optional Expiration**: Classic tokens can be set to never expire
   - Best practice: Set expiration and rotate regularly
   - GitHub will send notifications before expiry

## Permission Verification

### Testing Fine-Grained Token Permissions

```bash
# Test organization membership
curl -H "Authorization: Bearer $GH_PAT" \
     https://api.github.com/user/memberships/orgs/YOUR_ORG

# Test repository enumeration
curl -H "Authorization: Bearer $GH_PAT" \
     https://api.github.com/orgs/YOUR_ORG/repos?per_page=5

# Test issue search (if issues permission granted)
curl -H "Authorization: Bearer $GH_PAT" \
     https://api.github.com/search/issues?q=repo:YOUR_ORG/REPO+label:security
```

### Testing Classic Token Permissions

```bash
# Test organization access
curl -H "Authorization: Bearer $GH_PAT" \
     https://api.github.com/user/orgs

# Test repository access
curl -H "Authorization: Bearer $GH_PAT" \
     https://api.github.com/repos/YOUR_ORG/REPO
```

## Common Permission Issues

### 401 Unauthorized

**Causes:**
- Token has expired
- Token was revoked or deleted
- Wrong token format (ensure it starts with `ghp_` or `github_pat_`)

**Solutions:**
1. Verify token hasn't expired in GitHub Settings → Developer settings
2. Regenerate token and update `GH_PAT` repository secret
3. Check token format and ensure no extra spaces/characters

### 403 Forbidden

**Causes:**
- User is not a member of the organization
- Fine-grained token not scoped to the organization
- Fine-grained token missing required permissions
- Repository access not granted to token

**Solutions:**
1. Verify user is an organization member (not just outside collaborator)
2. For fine-grained tokens:
   - Check "Resource owner" is the organization
   - Verify "Repository access" includes target repositories
   - Confirm all required permissions are granted
3. For Classic tokens:
   - Ensure `repo` and `read:org` scopes are selected
   - Check if organization requires SSO authorization (re-authorize token)

### 404 Not Found

**Causes:**
- Repository doesn't exist
- User lacks access to private repository
- Organization name misspelled

**Solutions:**
1. Verify organization and repository names are correct
2. Confirm repository is not deleted or renamed
3. Check if repository is private and token has access

### Issues Not Being Created

**Causes:**
- Issues permission not granted (fine-grained tokens)
- Repository has issues disabled
- `open_issues: false` in workflow input

**Solutions:**
1. For fine-grained tokens: Grant "Issues: Read and write" permission
2. For Classic tokens: Ensure `repo` scope is selected (includes issues)
3. Verify repository has issues enabled in Settings → Features
4. Set `open_issues: true` in workflow inputs

## Security Best Practices

1. **Principle of Least Privilege**:
   - Use fine-grained tokens with minimum required permissions
   - For read-only scanning, omit Issues permission

2. **Token Rotation**:
   - Set expiration on all tokens (max 1 year recommended)
   - Rotate tokens before expiry using calendar reminders
   - Test new token before revoking old one

3. **Scope Isolation**:
   - Use separate tokens for different purposes
   - Scanning token: Contents (Read) + Members (Read)
   - Reporting token: Issues (Write) - if issues created via separate workflow

4. **Audit Logging**:
   - Monitor GitHub audit logs for token usage
   - Review workflow logs for authentication errors
   - Track which organizations are being scanned

5. **Secret Management**:
   - Store tokens in GitHub Secrets (never commit to repository)
   - Use organization-level secrets for multi-repo workflows
   - Rotate immediately if token is exposed

## References

- [Fine-Grained PAT Permissions](https://docs.github.com/en/rest/authentication/permissions-required-for-fine-grained-personal-access-tokens)
- [Creating Fine-Grained PATs](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens#creating-a-fine-grained-personal-access-token)
- [Creating Classic PATs](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens#creating-a-personal-access-token-classic)
- [OAuth Scopes](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps)
- [GitHub REST API Documentation](https://docs.github.com/en/rest)

---

**Last Updated**: 2025-10-28
**Status**: Complete and verified
