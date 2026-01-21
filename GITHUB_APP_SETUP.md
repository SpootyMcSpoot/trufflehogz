# GitHub App Authentication Setup

Guide for migrating from Personal Access Token (PAT) to GitHub App authentication.

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

## Implementation Changes

### Python Script Updates

Add GitHub App authentication support to `trufflehog_scanner.py`:

```python
import jwt
import time

def get_github_app_token(app_id: str, private_key: str, installation_id: str) -> str:
    """
    Generate installation access token from GitHub App credentials.
    
    Returns: Installation access token (valid for 1 hour)
    """
    # Create JWT for app authentication
    now = int(time.time())
    payload = {
        'iat': now,
        'exp': now + (10 * 60),  # JWT expires in 10 minutes
        'iss': app_id
    }
    
    jwt_token = jwt.encode(payload, private_key, algorithm='RS256')
    
    # Exchange JWT for installation access token
    GLOBAL_LIMITER.wait()
    headers = {
        'Authorization': f'Bearer {jwt_token}',
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28'
    }
    
    url = f"{API_BASE}/app/installations/{installation_id}/access_tokens"
    req = Request(url, method='POST', headers=headers)
    
    with urlopen(req, timeout=DEFAULT_API_TIMEOUT_SEC) as resp:
        data = json.loads(resp.read().decode('utf-8'))
        return data['token']

def get_auth_token() -> str:
    """
    Get authentication token from either GitHub App or PAT.
    
    Priority:
    1. GitHub App (GH_APP_ID + GH_APP_PRIVATE_KEY + GH_APP_INSTALLATION_ID)
    2. Classic PAT (GH_PAT)
    """
    app_id = os.environ.get('GH_APP_ID')
    private_key = os.environ.get('GH_APP_PRIVATE_KEY')
    installation_id = os.environ.get('GH_APP_INSTALLATION_ID')
    
    if app_id and private_key and installation_id:
        logging.info("Authenticating with GitHub App")
        return get_github_app_token(app_id, private_key, installation_id)
    
    pat = os.environ.get('GH_PAT')
    if pat:
        logging.info("Authenticating with PAT")
        return pat
    
    raise ValueError("No authentication method available. Set either GH_PAT or GitHub App credentials.")
```

Update the API helper functions to use the new `get_auth_token()`:

```python
def api_call(method: str, url: str, headers: Optional[Dict] = None, 
             data: Optional[bytes] = None, retries: int = DEFAULT_MAX_RETRIES) -> Any:
    """Make authenticated API call with retry logic."""
    token = get_auth_token()  # Get token dynamically
    
    if headers is None:
        headers = {}
    headers.update({
        'Authorization': f'Bearer {token}',  # Works for both PAT and App tokens
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28'
    })
    
    # ... rest of existing retry logic
```

### Workflow Updates

Update `.github/workflows/trufflehog-org-scan.yml`:

```yaml
env:
  # GitHub App credentials (preferred)
  GH_APP_ID: ${{ secrets.GH_APP_ID }}
  GH_APP_PRIVATE_KEY: ${{ secrets.GH_APP_PRIVATE_KEY }}
  GH_APP_INSTALLATION_ID: ${{ secrets.GH_APP_INSTALLATION_ID }}
  
  # Fallback to PAT if app credentials not available
  GH_PAT: ${{ secrets.GH_PAT }}
```

Update TruffleHog command to use app authentication:

```bash
# If using GitHub App, TruffleHog supports --token flag
if [ -n "${GH_APP_ID}" ]; then
  TOKEN=$(python3 -c "from trufflehog_scanner import get_auth_token; print(get_auth_token())")
  trufflehog github --org="${ORG}" --token="${TOKEN}" ...
else
  # Fallback to PAT
  trufflehog github --org="${ORG}" --token="${GH_PAT}" ...
fi
```

### Dependencies

Add to `requirements.txt`:

```
PyJWT>=2.8.0
cryptography>=41.0.0
```

Install in workflow:

```yaml
- name: Install Python dependencies
  run: |
    pip install --upgrade pip
    pip install PyJWT cryptography
```

## Testing

### Local Testing

```bash
export GH_APP_ID="123456"
export GH_APP_INSTALLATION_ID="789012"
export GH_APP_PRIVATE_KEY="$(cat path/to/private-key.pem)"

python3 trufflehog_scanner.py --ndjson findings.ndjson --dry-run
```

### Verify Authentication

```python
# Test script
import os
from trufflehog_scanner import get_auth_token, api_call

token = get_auth_token()
print(f"Token obtained: {token[:20]}...")

# Test API call
user = api_call('GET', 'https://api.github.com/user')
print(f"Authenticated as: {user['login']}")
print(f"Rate limit: {user.get('rate_limit', 'N/A')}")
```

## Migration Strategy

### Phase 1: Parallel Run
- Keep existing PAT
- Add GitHub App secrets
- Code falls back to PAT if app auth fails
- Monitor for 2-4 weeks

### Phase 2: Validation
- Verify app authentication working in logs
- Check rate limits improved
- Confirm issue creation working

### Phase 3: Cutover
- Remove fallback to PAT
- Delete `GH_PAT` secret
- Update documentation

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
