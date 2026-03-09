# GitHub App Authentication Setup

Guide for setting up GitHub App authentication for TruffleHog Organization Scanner.

## Why GitHub App?

**Benefits over PAT:**
- Scoped to specific organizations/repositories
- Higher rate limits (5,000 requests/hour vs 1,000 for PAT)
- Organization-level installation (no per-user dependency)
- Automatic token rotation
- Audit trail of app installations
- Can be managed by organization admins
- No expiration concerns

## Setup Steps

### 1. Create GitHub App

**Navigate**: Enterprise Settings → Developer settings → GitHub Apps → New GitHub App

**Configuration**:

| Field | Value |
|-------|-------|
| **GitHub App name** | `trufflehog-scanner-enterprise` |
| **Homepage URL** | Repository URL |
| **Webhook** | Uncheck "Active" |

**Permissions** (Repository):
- **Contents**: Read-only (access repository code for scanning)
- **Metadata**: Read-only (REQUIRED for private and internal repositories)
- **Issues**: Read and write (create and manage security issues)

**Permissions** (Organization):
- **Members**: Read-only (optional - only if you need org member enumeration)

**Note**: Do NOT enable "Administration: Read-only" unless specifically required. For scanning repos and creating issues, it's unnecessary and overly permissive.

**Where can this GitHub App be installed?**
- Any account (to allow installation across all enterprise orgs)

**Note**: Creating the app at enterprise level allows a single app to access all organizations within the enterprise with one installation.

**Create** the app.

### 2. Generate Private Key

1. On the app page, scroll to "Private keys"
2. Click "Generate a private key"
3. Save the `.pem` file securely

### 3. Install App to Enterprise/Organizations

**Enterprise-wide Installation** (Recommended for scanning multiple orgs):
1. On the app page, click "Install App"
2. Select your **enterprise** (not individual organizations)
3. Choose "All organizations" to enable for all orgs in enterprise
4. Select "All repositories" (recommended for comprehensive scanning)
5. Click "Install"
6. Note the **Installation ID** from the URL: `enterprises/{enterprise}/settings/installations/{INSTALLATION_ID}`

**Important**: 
- Enterprise-wide installation provides ONE installation ID that accesses ALL organizations
- This is required for scanning internal and private repositories across multiple orgs
- The installation ID works for all repos in all orgs within the enterprise
- You do NOT need separate installations per organization

**Per-Organization Installation** (Alternative - only if not using enterprise):
1. On the app page, click "Install App"
2. Select each organization individually
3. Choose "All repositories" or select specific repos
4. Click "Install"
5. Repeat for each organization
6. **Limitation**: Each org has its own installation ID, requiring separate configurations

### 4. Get App Credentials

From the app settings page, note:
- **App ID** (at the top of the page)
- **Installation ID**:
  - Enterprise: `enterprises/{enterprise}/settings/installations/{ID}`
  - Organization: `organizations/{org}/settings/installations/{ID}`
  - Or use API: `curl -H "Authorization: Bearer <JWT>" https://api.github.com/app/installations`

### 5. Store Secrets in Repository

Repository Settings → Secrets and variables → Actions → Secrets

Add three secrets:

| Name | Value |
|------|-------|
| `GH_APP_ID` | Your App ID |
| `GH_APP_INSTALLATION_ID` | Your Installation ID |
| `GH_APP_PRIVATE_KEY` | Contents of the `.pem` file |

## How It Works

GitHub App authentication is **already implemented** in the scanner. When secrets `GH_APP_ID`, `GH_APP_PRIVATE_KEY`, and `GH_APP_INSTALLATION_ID` are set, the scanner:

1. Creates a JWT signed with the private key (10-minute lifetime)
2. Exchanges the JWT for an installation access token (1-hour lifetime)
3. Uses the installation token for both TruffleHog scanning and GitHub API calls (issue creation, dedup checks)
4. Falls back to `GH_PAT` if app credentials are not available

The workflow automatically passes app credentials as environment variables — no configuration changes needed beyond adding the three secrets.

### Dependencies

Already included in `requirements.txt`:

```
PyJWT>=2.10.0
cryptography>=45.0.0
```

## Testing

### Verify Authentication Locally

```bash
export GH_APP_ID="123456"
export GH_APP_INSTALLATION_ID="789012"
export GH_APP_PRIVATE_KEY="$(cat path/to/private-key.pem)"

# Dry-run to test auth without creating issues
python3 trufflehog_scanner.py --ndjson findings.ndjson --dry-run
```

### Verify in Workflow

Run the self-scan workflow (**Actions → Self-Scan Test → Run workflow**). If it passes, authentication and scanning are working correctly.

## Troubleshooting

**401 Unauthorized with App**:
- Verify App ID is correct
- Check private key format (must include `-----BEGIN RSA PRIVATE KEY-----`)
- Ensure installation ID matches the enterprise/org installation

**403 Forbidden**:
- App not installed to enterprise/organization
- Missing **Metadata: Read-only** permission (required for private/internal repos)
- Missing **Contents: Read-only** permission
- For enterprise: Check app is installed enterprise-wide, not per-org
- Verify app has "Any account" installation setting enabled
- Confirm the installation covers the specific repository being scanned

**JWT Errors**:
- Private key may be corrupted
- Check key includes full PEM format with headers
- Ensure no extra whitespace in secret

**Token Expiration**:
- Installation tokens expire after 1 hour
- Script should regenerate token automatically
- For long-running scans, implement token refresh logic

## Security Considerations

1. **Private Key Storage**: Never commit `.pem` files to git
2. **Secret Rotation**: Rotate app private keys annually
3. **Installation Scope**: Limit to necessary repositories when possible
4. **Audit Logs**: Monitor app usage in organization settings
5. **Permissions**: Request minimum permissions needed

## Rate Limits

| Auth Method | Rate Limit | Reset Window |
|-------------|-----------|--------------|
| PAT | 1,000/hour | 1 hour |
| GitHub App (Org) | 5,000/hour | 1 hour |
| GitHub App (Enterprise) | 15,000/hour | 1 hour |

**Note**: Enterprise-wide GitHub Apps receive the 15,000/hour rate limit, making them ideal for scanning multiple organizations.

Check current limits:
```bash
curl -H "Authorization: Bearer $TOKEN" \
  https://api.github.com/rate_limit
```

## References

- [GitHub Apps Documentation](https://docs.github.com/en/apps)
- [Authenticating with GitHub Apps](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app)
- [Rate Limits for GitHub Apps](https://docs.github.com/en/rest/overview/rate-limits-for-the-rest-api)
