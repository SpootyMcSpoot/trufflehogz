#!/usr/bin/env python3
"""
TruffleHog findings -> per-repo GitHub issues, with diagnostics and false-positive filtering.

Key features
- Multi-threaded issue creation (ThreadPoolExecutor)
- Structured logging with per-thread context
- GitHub API retries with backoff and a simple global rate limiter
- Auto-labels based on detector type and severity; creates missing labels only when needed
- De-dupes repeated findings; only Verified==True are eligible for issues
- DRY-RUN support
- Sanitized console preview and Markdown summary (with commit/line links)
- Diagnostics for NDJSON (per-repo and merged)
- False-positive filtering via regex allow-list file (--exclude-patterns-file)
- Detailed per-repo subsection in summary (detector + link to commit/line)
- Optional errors NDJSON inclusion (HTTP errors etc.) into the summary

Environment
  GH_APP_ID              GitHub App ID (preferred)
  GH_APP_PRIVATE_KEY     GitHub App private key (PEM format)
  GH_APP_INSTALLATION_ID GitHub App installation ID
  GH_PAT                 GitHub Classic PAT with repo and read:org (fallback)
  GH_TOKEN               GitHub Actions token (fallback, also checks GITHUB_TOKEN)

CLI
  --ndjson PATH          TruffleHog NDJSON path (default: findings.ndjson)
  --run-url URL          Link back to the workflow run
  --max-workers N        Thread count (default: min(8, cpu_count()*2))
  --labels "a,b,c"       Extra labels to union with auto labels
  --dry-run              Do not write to GitHub
  --title-prefix STR     Issue title prefix (default: "[TruffleHog]")
  --log-level LEVEL      Logging level (default: INFO)
  --print-sanitized N    Print up to N sanitized VERIFIED finding lines to stdout
  --write-summary        Write a Markdown summary to GITHUB_STEP_SUMMARY
  --org STR              Optional org name for display in logs
  --errors-ndjson PATH   Optional NDJSON of errors (e.g., HTTP 401) to include in summary

Diagnostics
  --diag-dir PATH        Directory of per-repo .ndjson to inspect
  --diag-max-files N     Max number of files to print diagnostics for (default: 25)
  --diag-max-lines N     Max sanitized lines to print per file (default: 3)
  --validate             Print stats about --ndjson (total lines, bad lines, unique repos/detectors) and exit (read-only)

False-positive filtering
  --exclude-patterns-file PATH
                         Path to newline-delimited regexes (comments starting with #).
                         If a regex matches the composed check string for a finding,
                         that finding is ignored in logs, summary, and issue creation.

Summary detail controls
  --summary-detailed             Include a per-repo detailed subsection (detector + link)
  --summary-detailed-per-repo N  Max rows per repo in detailed subsection (default: 10)
  --summary-detailed-max N       Max total rows overall across all repos (default: 300)
"""

from __future__ import annotations

import argparse
import collections
import concurrent.futures as cf
import datetime
import hashlib
import json
import logging
import os
import random
import re
import sys
import threading
import time
from typing import Any, Dict, Generator, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus, quote as urlquote, urlparse
from urllib.request import Request, urlopen

try:
    import jwt
    JWT_AVAILABLE = True
except ImportError:
    JWT_AVAILABLE = False
    logging.warning("PyJWT not available, GitHub App authentication disabled")

# ---------------------------- Helper functions ----------------------------

def _safe_int_env(name: str, default: int) -> int:
    """Safely parse an integer environment variable, returning default on failure."""
    try:
        return int(os.environ.get(name, str(default)))
    except (ValueError, TypeError):
        return default

def _severity_rank(sev: str) -> int:
    """Get numeric rank for severity label, with safe default for unknown values."""
    try:
        return SEVERITY_ORDER.index(sev)
    except ValueError:
        return 1  # Default to sec:medium rank

# ---------------------------- Configuration constants ----------------------------

API_BASE = "https://api.github.com"

# API rate limiting and retry configuration
DEFAULT_RATE_LIMIT_PER_SEC = 4.0  # GitHub API calls per second
DEFAULT_API_TIMEOUT_SEC = 30      # Timeout for individual API calls
DEFAULT_MAX_RETRIES = 5           # Maximum retry attempts for failed API calls
DEFAULT_BACKOFF_BASE = 1.5        # Exponential backoff base multiplier
MAX_BACKOFF_SEC = 16.0            # Maximum backoff sleep time

# Issue deduplication window
DEFAULT_ISSUE_DEDUP_DAYS = 7      # Days to search for existing issues

# ---------------------------- Rate limiting ----------------------------

class RateLimiter:
    def __init__(self, calls_per_sec: float = DEFAULT_RATE_LIMIT_PER_SEC):
        self.interval = 1.0 / max(0.1, abs(calls_per_sec) if calls_per_sec else DEFAULT_RATE_LIMIT_PER_SEC)
        self.lock = threading.Lock()
        self.last = 0.0

    def wait(self) -> None:
        # Calculate wait time while holding lock, then release before sleeping
        # to avoid blocking other threads unnecessarily
        wait_for = 0.0
        with self.lock:
            now = time.time()
            wait_for = self.last + self.interval - now
            if wait_for > 0:
                self.last = now + wait_for  # Reserve our slot
            else:
                self.last = now
        if wait_for > 0:
            time.sleep(wait_for)

GLOBAL_LIMITER = RateLimiter(calls_per_sec=DEFAULT_RATE_LIMIT_PER_SEC)

# ---------------------------- GitHub App Authentication ----------------------------

def get_github_app_token(app_id: str, private_key: str, installation_id: str) -> str:
    """
    Generate installation access token from GitHub App credentials.
    
    Returns: Installation access token (valid for 1 hour)
    """
    if not JWT_AVAILABLE:
        raise RuntimeError("PyJWT is required for GitHub App authentication. Install with: pip install PyJWT")
    
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
    
    try:
        with urlopen(req, timeout=DEFAULT_API_TIMEOUT_SEC) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            token = data.get('token')
            if not token:
                raise ValueError("No token in GitHub App response")
            logging.info("Successfully authenticated with GitHub App (installation_id=%s)", installation_id)
            return token
    except HTTPError as e:
        logging.error("Failed to authenticate with GitHub App: HTTP %d", e.code)
        raise
    except Exception as e:
        logging.error("Failed to authenticate with GitHub App: %s", e)
        raise

def get_auth_token() -> str:
    """
    Get authentication token from either GitHub App or PAT.
    
    Priority:
    1. GitHub App (GH_APP_ID + GH_APP_PRIVATE_KEY + GH_APP_INSTALLATION_ID)
    2. Classic PAT (GH_PAT)
    3. GitHub Actions token (GH_TOKEN or GITHUB_TOKEN)
    
    Returns: Authentication token
    Raises: ValueError if no authentication method available
    """
    app_id = os.environ.get('GH_APP_ID')
    private_key = os.environ.get('GH_APP_PRIVATE_KEY')
    installation_id = os.environ.get('GH_APP_INSTALLATION_ID')
    
    if app_id and private_key and installation_id:
        try:
            return get_github_app_token(app_id, private_key, installation_id)
        except Exception as e:
            logging.warning("GitHub App authentication failed, falling back to PAT: %s", e)
    
    pat = os.environ.get('GH_PAT')
    if pat:
        logging.info("Authenticating with PAT")
        return pat
    
    # Also check GH_TOKEN and GITHUB_TOKEN for GitHub Actions compatibility
    gh_token = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    if gh_token:
        logging.info("Authenticating with GH_TOKEN/GITHUB_TOKEN")
        return gh_token
    
    raise ValueError("No authentication method available. Set either GH_PAT, GH_TOKEN, or GitHub App credentials (GH_APP_ID, GH_APP_PRIVATE_KEY, GH_APP_INSTALLATION_ID)")

# ---------------------------- Logging ----------------------------

def setup_logging(level: str = "INFO") -> None:
    """Configure root logger with timestamp, level, thread, and function context."""
    lvl = getattr(logging, level.upper(), logging.INFO)
    fmt = "%(asctime)s %(levelname)s %(threadName)s %(funcName)s: %(message)s"
    logging.basicConfig(level=lvl, format=fmt)

# ---------------------------- Label taxonomy ----------------------------

# Applied to every issue by default, regardless of detector type.
DEFAULT_LABELS = ["security", "tool:trufflehog"]

# Ordered low-to-high so index position doubles as a numeric rank.
SEVERITY_ORDER = ["sec:low", "sec:medium", "sec:high", "sec:critical"]

# Maps TruffleHog detector names to GitHub issue severity labels.
# The highest severity across all findings in a repo determines the issue's label.
DETECTOR_SEVERITY = {
    "AWS": "sec:critical",
    "GCP": "sec:critical",
    "Azure": "sec:high",
    "GitHub": "sec:high",
    "Slack": "sec:high",
    "SlackWebhook": "sec:high",
    "Stripe": "sec:high",
    "Twilio": "sec:high",
    "SendGrid": "sec:high",
    "Mailchimp": "sec:medium",
    "Private Key": "sec:critical",
    "SSH": "sec:critical",
    "Database": "sec:high",
    "JWT": "sec:medium",
    "Kubernetes": "sec:high",
    "Docker": "sec:medium",
    "Generic": "sec:medium",
}

# Maps detector names to type labels (e.g. "secret:aws").
# These are informational -- not currently applied to issues to avoid label clutter,
# but used by normalize_detector() for consistent naming across the codebase.
DETECTOR_TYPES = {
    "AWS": "secret:aws",
    "GCP": "secret:gcp",
    "Azure": "secret:azure",
    "GitHub": "secret:github",
    "Slack": "secret:slack",
    "SlackWebhook": "secret:slack-webhook",
    "Stripe": "secret:stripe",
    "Twilio": "secret:twilio",
    "SendGrid": "secret:sendgrid",
    "Mailchimp": "secret:mailchimp",
    "Private Key": "secret:private-key",
    "SSH": "secret:ssh",
    "Database": "secret:db",
    "JWT": "secret:jwt",
    "Kubernetes": "secret:kube",
    "Docker": "secret:docker",
    "Generic": "secret:generic",
}

# Suppression labels — applied to issues to signal the scanner to skip findings
# When an issue has one of these labels, its findings hash is treated as suppressed.
SUPPRESSION_LABELS = {
    "trufflehog:false-positive": {"color": "cfd3d7", "description": "Not a real secret — scanner false positive"},
    "trufflehog:accepted-risk": {"color": "fbca04", "description": "Team acknowledges and accepts the risk"},
    "trufflehog:wont-fix": {"color": "ffffff", "description": "Will not be remediated (test fixture, revoked, etc.)"},
    "trufflehog:remediated": {"color": "0e8a16", "description": "Secret has been rotated/removed"},
}
SUPPRESSION_LABEL_NAMES = set(SUPPRESSION_LABELS.keys())

# Comment commands that map to suppression labels
COMMENT_COMMANDS = {
    "/trufflehog false-positive": "trufflehog:false-positive",
    "/trufflehog accepted-risk": "trufflehog:accepted-risk",
    "/trufflehog wont-fix": "trufflehog:wont-fix",
    "/trufflehog remediated": "trufflehog:remediated",
}

# I5: Detector-specific remediation guidance for issue bodies
DETECTOR_REMEDIATION = {
    "AWS": (
        "1. **Deactivate** the key in [IAM Console](https://console.aws.amazon.com/iam/) → Users → Security credentials\n"
        "2. **Delete** the compromised access key\n"
        "3. **Create** a new key and update all services using it\n"
        "4. Review [CloudTrail](https://console.aws.amazon.com/cloudtrail/) for unauthorized usage"
    ),
    "GCP": (
        "1. **Revoke** the service account key in [GCP Console](https://console.cloud.google.com/iam-admin/serviceaccounts)\n"
        "2. **Delete** the compromised key JSON\n"
        "3. **Create** a new key and redeploy\n"
        "4. Review [Cloud Audit Logs](https://console.cloud.google.com/logs) for unauthorized access"
    ),
    "Azure": (
        "1. **Regenerate** the key/secret in [Azure Portal](https://portal.azure.com/)\n"
        "2. **Update** all applications using the old credential\n"
        "3. Review [Azure Activity Log](https://portal.azure.com/#blade/Microsoft_Azure_ActivityLog) for unauthorized usage"
    ),
    "GitHub": (
        "1. **Revoke** the token at [GitHub Settings → Tokens](https://github.com/settings/tokens)\n"
        "2. **Create** a new fine-grained token with minimum required permissions\n"
        "3. Review [Security Log](https://github.com/settings/security-log) for unauthorized API usage"
    ),
    "Slack": (
        "1. **Revoke** the token in [Slack App Management](https://api.slack.com/apps)\n"
        "2. **Generate** a new token with required scopes\n"
        "3. Review Slack audit logs for unauthorized message access"
    ),
    "SlackWebhook": (
        "1. **Delete** the webhook in [Slack App → Incoming Webhooks](https://api.slack.com/apps)\n"
        "2. **Create** a new webhook URL and update integrations"
    ),
    "Stripe": (
        "1. **Roll** the API key in [Stripe Dashboard](https://dashboard.stripe.com/apikeys)\n"
        "2. **Update** all services using the old key\n"
        "3. Review [Stripe event logs](https://dashboard.stripe.com/events) for unauthorized activity"
    ),
    "SendGrid": (
        "1. **Revoke** the API key in [SendGrid Settings](https://app.sendgrid.com/settings/api_keys)\n"
        "2. **Create** a new key with minimum required permissions"
    ),
    "Private Key": (
        "1. **Revoke** any certificates signed with this key\n"
        "2. **Generate** a new key pair: `ssh-keygen -t ed25519` or `openssl genrsa -out new_key.pem 4096`\n"
        "3. **Re-issue** certificates or deploy the new public key\n"
        "4. **Remove** the compromised key from all authorized_keys / trust stores"
    ),
    "SSH": (
        "1. **Remove** the key from `~/.ssh/authorized_keys` on all servers\n"
        "2. **Generate** a new key: `ssh-keygen -t ed25519 -C 'replacement'`\n"
        "3. **Deploy** the new public key to required hosts\n"
        "4. Review SSH access logs (`/var/log/auth.log`) for unauthorized logins"
    ),
    "Database": (
        "1. **Rotate** the database password immediately\n"
        "2. **Update** connection strings in all services / secret managers\n"
        "3. **Review** database access logs for unauthorized queries\n"
        "4. Consider restricting access to IP allowlists"
    ),
    "Kubernetes": (
        "1. **Rotate** the service account token / kubeconfig\n"
        "2. **Delete** the compromised Secret: `kubectl delete secret <name>`\n"
        "3. Review `kubectl get events` and audit logs for unauthorized API calls"
    ),
    "Twilio": (
        "1. **Rotate** the Auth Token in [Twilio Console](https://www.twilio.com/console)\n"
        "2. **Update** all applications using the old credentials"
    ),
}

def normalize_detector(name: str) -> str:
    n = (name or "").lower()
    for k in DETECTOR_TYPES:
        if k.lower() in n:
            return k
    if "pem" in n or "private key" in n:
        return "Private Key"
    return "Generic"

def highest_severity(items: List[Dict[str, Any]]) -> str:
    """Return the highest severity label across all items."""
    highest = "sec:low"
    for it in items:
        det = normalize_detector(str(it.get("detector", "")))
        sev = DETECTOR_SEVERITY.get(det, "sec:medium")
        if _severity_rank(sev) > _severity_rank(highest):
            highest = sev
    return highest

def labels_for_items(items: List[Dict[str, Any]], extra: Optional[List[str]] = None) -> List[str]:
    """Build a concise label set: base labels + highest severity only + needs-triage.

    Previous approach added every unique severity AND every detector-type label,
    producing 10-15 labels per issue.  Now we keep only the highest severity
    (the most actionable signal) and leave detector detail in the issue body.
    """
    labels = set(DEFAULT_LABELS)
    sev = highest_severity(items)
    labels.add(sev)
    labels.add("needs-triage")
    if extra:
        labels.update([s.strip() for s in extra if s.strip()])
    return sorted(labels)

# ---------------------------- Helpers to read fields ----------------------------

def deepget(obj: Dict[str, Any], path: List[str]) -> Optional[Any]:
    cur: Any = obj
    for k in path:
        if isinstance(cur, dict) and k in cur:
            cur = cur[k]
        else:
            return None
    return cur

def sanitize_repo(repo: str) -> str:
    if not repo or "/" not in repo:
        return repo or ""
    owner, name = repo.split("/", 1)
    if name.lower().endswith(".git"):
        name = name[:-4]
    return f"{owner}/{name}"

def extract_repo(owner_repo_or_url: Optional[str]) -> Optional[str]:
    if not owner_repo_or_url:
        return None
    s = str(owner_repo_or_url).strip()
    if s.startswith("http"):
        p = urlparse(s)
        parts = [x for x in p.path.split("/") if x]
        if len(parts) >= 2:
            return sanitize_repo(f"{parts[0]}/{parts[1]}")
    if "/" in s:
        parts = s.split("/")
        if len(parts) >= 2:
            return sanitize_repo(f"{parts[0]}/{parts[1]}")
    return None

def find_repo(o: Dict[str, Any]) -> Optional[str]:
    candidates = [
        deepget(o, ["SourceMetadata", "Data", "Git", "repository"]),
        deepget(o, ["SourceMetadata", "Data", "Github", "repository"]),  # trufflehog uses lowercase 'h'
        deepget(o, ["SourceMetadata", "Data", "GitHub", "repository"]),  # also check capital H for compat
        deepget(o, ["SourceMetadata", "Data", "repository"]),
    ]
    for c in candidates:
        repo = extract_repo(c)
        if repo:
            return repo
    m = re.search(r'https://github\.com/([^/]+)/([^/"\s]+)', json.dumps(o))
    if m:
        return sanitize_repo(f"{m.group(1)}/{m.group(2)}")
    return None

def file_path(o: Dict[str, Any]) -> str:
    for path in (
        ["SourceMetadata", "Data", "Git", "file"],
        ["SourceMetadata", "Data", "Github", "file"],
        ["SourceMetadata", "Data", "GitHub", "file"],
        ["SourceMetadata", "Data", "Filesystem", "file"],
        ["SourceMetadata", "Data", "file"],
    ):
        v = deepget(o, path)
        if v:
            return str(v)
    return ""

def line_no(o: Dict[str, Any]) -> Optional[int]:
    for path in (
        ["SourceMetadata", "Data", "Git", "line"],
        ["SourceMetadata", "Data", "Github", "line"],
        ["SourceMetadata", "Data", "GitHub", "line"],
        ["SourceMetadata", "Data", "Filesystem", "line"],
        ["SourceMetadata", "Data", "line"],
    ):
        v = deepget(o, path)
        if isinstance(v, int) or (isinstance(v, str) and v.isdigit()):
            return int(v)
    return None

def commit_sha(o: Dict[str, Any]) -> Optional[str]:
    for path in (
        ["SourceMetadata", "Data", "Git", "commit"],
        ["SourceMetadata", "Data", "Github", "commit"],
        ["SourceMetadata", "Data", "GitHub", "commit"],
        ["SourceMetadata", "Data", "commit"],
    ):
        v = deepget(o, path)
        if v:
            return str(v)
    return None

def shortpath(p: str) -> str:
    p = str(p or "").strip()
    if not p:
        return ""
    parts = [x for x in p.split("/") if x]
    if len(parts) <= 3:
        return "/".join(parts)
    return ".../" + "/".join(parts[-3:])

def line_link(repo: str, commit: Optional[str], path: str, line: Optional[int]) -> str:
    """
    Generate a GitHub link to the file WITHOUT line anchor.

    SECURITY: We intentionally omit the #L{line} anchor to prevent GitHub from
    automatically showing code previews that would expose the secret in the issue.
    Users can still find the exact line by looking at the Line column in the table.
    """
    if repo and path:
        # Strip container/filesystem prefixes (e.g. /repo/) for clean GitHub URLs
        clean = re.sub(r'^/repo/', '', path).lstrip('/')
        quoted = urlquote(clean, safe="/")
        # NOTE: No line anchor to prevent GitHub's auto code preview
        if commit:
            return f"https://github.com/{repo}/blob/{commit}/{quoted}"
        else:
            return f"https://github.com/{repo}/blob/HEAD/{quoted}"
    if repo and commit:
        return f"https://github.com/{repo}/commit/{commit}"
    return ""

def blame_link(repo: str, commit: Optional[str], path: str, line: Optional[int]) -> str:
    """Generate a GitHub blame link WITH line anchor.

    Blame view is safe to deep-link (no auto code-preview expansion in issues)
    and the line anchor helps reviewers jump straight to the responsible commit.
    """
    if repo and path:
        # Strip container/filesystem prefixes (e.g. /repo/) for clean GitHub URLs
        clean = re.sub(r'^/repo/', '', path).lstrip('/')
        quoted = urlquote(clean, safe="/")
        ref = commit or "HEAD"
        anchor = f"#L{line}" if line else ""
        return f"https://github.com/{repo}/blame/{ref}/{quoted}{anchor}"
    return ""

# ---------------------------- False-positive filtering ----------------------------

def load_exclude_regexes(path: Optional[str]) -> List[re.Pattern]:
    regs: List[re.Pattern] = []
    if not path:
        return regs
    if not os.path.isfile(path):
        logging.warning("Exclude patterns file not found: %s", path)
        return regs
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            try:
                regs.append(re.compile(s, re.IGNORECASE))
            except re.error as e:
                logging.warning("Bad regex ignored: %r error=%s", s, e)
    logging.info("Loaded %d exclude pattern(s) from %s", len(regs), path)
    return regs

def compose_match_string(o: Dict[str, Any]) -> str:
    """Build a pipe-delimited string from a finding for regex matching.
    
    This is what false-positive regexes are matched against. Each field is
    prefixed so patterns can target specific attributes, e.g.:
      detector=AWS | verified=True | repo=org/repo | file=path/to.py | redacted=AKIA...
    """
    det = o.get("DetectorName") or o.get("DetectorType") or "Unknown"
    repo = find_repo(o) or ""
    repo = sanitize_repo(repo)
    fpath = file_path(o)
    redacted = o.get("Redacted") or ""
    ver = o.get("Verified")
    parts = [
        f"detector={det}",
        f"verified={ver}",
        f"repo={repo}",
        f"file={fpath}",
        f"redacted={redacted}",
    ]
    return " | ".join(parts)

def is_excluded(o: Dict[str, Any], regexes: List[re.Pattern]) -> bool:
    """Check if a finding matches any false-positive regex pattern."""
    if not regexes:
        return False
    s = compose_match_string(o)
    for rx in regexes:
        if rx.search(s):
            return True
    return False

# ---------------------------- GitHub API ----------------------------

class GHClient:
    """GitHub API client with rate limiting, retries, and multi-org App auth.
    
    Wraps urllib to call the GitHub REST API. Supports both PAT and GitHub App
    authentication. For App auth, tokens are org-scoped: call for_org() to get
    a client with the correct installation token for a given org.
    
    All mutating calls (POST/PATCH/PUT/DELETE) are skipped in dry_run mode.
    GET calls always execute (needed for dedup checks even in dry-run).
    """
    def __init__(self, token: str, dry_run: bool = False,
                 app_id: str = "", app_private_key: str = ""):
        self.token = token
        self.api_base = API_BASE
        self.dry_run = dry_run
        self._app_id = app_id
        self._app_private_key = app_private_key
        self._org_token_cache: Dict[str, str] = {}  # org -> installation access token (GIL-safe for simple dict ops; worst case is a duplicate token mint)

    def _get_installation_for_org(self, org: str) -> int:
        """Look up the GitHub App installation ID for a given org via the App JWT."""
        if not JWT_AVAILABLE:
            raise RuntimeError("PyJWT required for multi-org App auth")
        now = int(time.time())
        payload = {'iat': now, 'exp': now + (10 * 60), 'iss': self._app_id}
        jwt_token = jwt.encode(payload, self._app_private_key, algorithm='RS256')

        GLOBAL_LIMITER.wait()
        url = f"{self.api_base}/orgs/{org}/installation"
        req = Request(url, method='GET')
        req.add_header('Authorization', f'Bearer {jwt_token}')
        req.add_header('Accept', 'application/vnd.github+json')
        req.add_header('X-GitHub-Api-Version', '2022-11-28')
        with urlopen(req, timeout=DEFAULT_API_TIMEOUT_SEC) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            installation_id = data.get('id')
            logging.info("Found installation %s for org %s", installation_id, org)
            return installation_id

    def for_org(self, org: str) -> 'GHClient':
        """Return a GHClient with an installation token scoped to the target org.

        GitHub App installation tokens are org-scoped: a token minted for org A
        cannot access private repos in org B.  When the scanner processes findings
        across multiple orgs, each org needs its own token.

        Falls back to the default token when App credentials are not available
        (e.g. PAT-based auth) or when the lookup fails.
        """
        if not self._app_id or not self._app_private_key:
            return self  # PAT auth — token already works cross-org

        if org in self._org_token_cache:
            return GHClient(token=self._org_token_cache[org], dry_run=self.dry_run)

        try:
            installation_id = self._get_installation_for_org(org)
            token = get_github_app_token(self._app_id, self._app_private_key, str(installation_id))
            self._org_token_cache[org] = token
            logging.info("Obtained org-scoped token for %s (installation=%s)", org, installation_id)
            return GHClient(token=token, dry_run=self.dry_run)
        except Exception as e:
            logging.warning("Failed to get org-scoped token for %s, using default: %s", org, e)
            return self

    def _req(self, method: str, path: str, payload: Optional[dict], attempt: int) -> dict:
        """Execute a single HTTP request against the GitHub API.
        
        Waits on the global rate limiter before each call. Returns parsed JSON.
        Raises HTTPError or URLError on failure (caller handles retries).
        """
        GLOBAL_LIMITER.wait()
        url = f"{self.api_base}{path}"
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Accept", "application/vnd.github+json")
        try:
            with urlopen(req, timeout=DEFAULT_API_TIMEOUT_SEC) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body) if body.strip() else {}
        except HTTPError as e:
            reset_hint = e.headers.get("X-RateLimit-Reset")
            remain = e.headers.get("X-RateLimit-Remaining")
            logging.warning(
                "HTTPError %s on %s %s (remain=%s reset=%s attempt=%d)",
                e.code, method, path, remain, reset_hint, attempt
            )
            raise
        except URLError as e:
            logging.warning("URLError on %s %s attempt=%d error=%s", method, path, attempt, e)
            raise

    def call(self, method: str, path: str, payload: Optional[dict] = None,
             max_retries: int = DEFAULT_MAX_RETRIES, backoff_base: float = DEFAULT_BACKOFF_BASE) -> dict:
        """Call the GitHub API with automatic retries and exponential backoff.
        
        Certain HTTP status codes (400, 401, 404, 422) are not retried because
        they indicate permanent errors (bad request, auth failure, not found,
        validation error). All other errors are retried up to max_retries times.
        """
        if self.dry_run and method.upper() in ("POST", "PATCH", "PUT", "DELETE"):
            logging.info("DRY RUN: %s %s payload=%s", method, path, payload)
            return {}
        for attempt in range(1, max_retries + 1):
            try:
                return self._req(method, path, payload, attempt)
            except HTTPError as e:
                if e.code in (400, 401, 404, 422):
                    # Bubble known errors (e.g., 401 auth, 404 no permission to labels)
                    raise
                if attempt >= max_retries:
                    logging.error("Max retries reached for %s %s", method, path)
                    raise
                sleep_s = min(MAX_BACKOFF_SEC, backoff_base ** attempt) + random.uniform(0, 0.5)
                time.sleep(sleep_s)
            except URLError:
                if attempt >= max_retries:
                    logging.error("Max retries reached for %s %s", method, path)
                    raise
                sleep_s = min(MAX_BACKOFF_SEC, backoff_base ** attempt) + random.uniform(0, 0.5)
                time.sleep(sleep_s)
        return {}

    def repo_meta(self, repo: str) -> Optional[dict]:
        try:
            return self.call("GET", f"/repos/{repo}", None)
        except HTTPError as e:
            logging.warning("Failed to get repo meta for %s: HTTP %d", repo, e.code)
            return None

    def search_issue_by_title(self, repo: str, title: str) -> bool:
        """
        Search for an issue (open or closed) by exact title match using Issues API.

        Note: Uses /repos/{repo}/issues instead of /search/issues because
        fine-grained PATs may not have access to the Search API.

        Searches both open and closed issues to prevent re-creating issues
        for findings that were previously reported.
        """
        try:
            # Check both open and closed issues (state=all)
            # We check first 2 pages (60 issues) to cover recent history
            for page in [1, 2]:
                data = self.call("GET", f"/repos/{repo}/issues?state=all&per_page=30&page={page}", None)
                if isinstance(data, list):
                    for issue in data:
                        if isinstance(issue, dict) and issue.get("title") == title:
                            state = issue.get("state", "unknown")
                            logging.info("Found existing issue in %s with title: %s (state=%s)", repo, title, state)
                            return True
                    # If we got fewer than 30 results, no need to check next page
                    if len(data) < 30:
                        break
            return False
        except HTTPError as e:
            logging.warning("Failed to list issues in %s: HTTP %d (may create duplicate)", repo, e.code)
            return False

    def search_recent_issues(self, repo: str, title_pattern: str, days: int = DEFAULT_ISSUE_DEDUP_DAYS) -> bool:
        """
        Search for recent issues matching a title pattern using Issues API.

        Note: Uses /repos/{repo}/issues instead of /search/issues because
        fine-grained PATs may not have access to the Search API.
        """
        # Use timezone-aware datetime to match GitHub API timestamps
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
        try:
            # List open issues sorted by created date
            # Check first 2 pages (60 issues) for recent matches
            for page in [1, 2]:
                data = self.call("GET", f"/repos/{repo}/issues?state=open&sort=created&direction=desc&per_page=30&page={page}", None)
                if not isinstance(data, list):
                    break

                for issue in data:
                    if not isinstance(issue, dict):
                        continue

                    # Check if title matches pattern
                    issue_title = issue.get("title", "")
                    if title_pattern not in issue_title:
                        continue

                    # Check if within date range
                    created_at = issue.get("created_at", "")
                    if created_at:
                        try:
                            created = datetime.datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                            if created >= cutoff:
                                logging.info("Found recent open issue in %s matching '%s' (created %s)",
                                           repo, title_pattern, created_at)
                                return True
                        except (ValueError, AttributeError):
                            pass

                # If we got fewer than 30 results, no need to check next page
                if len(data) < 30:
                    break

            return False
        except HTTPError as e:
            logging.warning("Failed to list recent issues in %s: HTTP %d", repo, e.code)
            return False

    def search_recent_issues_with_hash(self, repo: str, title_pattern: str, hash_pattern: str, days: int = DEFAULT_ISSUE_DEDUP_DAYS) -> bool:
        """
        Search for issues (open or closed) matching both title and hash patterns using Issues API.

        Looks for issues with the same findings hash to prevent duplicates even if
        reported on different days or if the original issue was closed.

        Searches both open and closed issues to avoid re-notifying about the same
        secrets that were already reported.
        """
        # Use timezone-aware datetime to match GitHub API timestamps
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
        try:
            # List ALL issues (open and closed) sorted by created date
            # E6: Reduced from 5 pages to 2 (exact-match title handles most dupes now)
            for page in [1, 2]:
                data = self.call("GET", f"/repos/{repo}/issues?state=all&sort=created&direction=desc&per_page=30&page={page}", None)
                if not isinstance(data, list):
                    break

                for issue in data:
                    if not isinstance(issue, dict):
                        continue

                    # Check if title matches both patterns
                    issue_title = issue.get("title", "")
                    if title_pattern not in issue_title or hash_pattern not in issue_title:
                        continue

                    # Check if within date range
                    created_at = issue.get("created_at", "")
                    if created_at:
                        try:
                            created = datetime.datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                            if created >= cutoff:
                                state = issue.get("state", "unknown")
                                logging.info("Found existing issue in %s with same findings hash '%s' (created %s, state=%s)",
                                           repo, hash_pattern, created_at, state)
                                return True
                        except (ValueError, AttributeError):
                            pass

                # If we got fewer than 30 results, no need to check next page
                if len(data) < 30:
                    break

            return False
        except HTTPError as e:
            logging.warning("Failed to list recent issues with hash in %s: HTTP %d", repo, e.code)
            return False

    def get_labels(self, repo: str) -> List[str]:
        names: List[str] = []
        page = 1
        max_pages = 10  # Guard against infinite loops (1000 labels should be enough)
        while page <= max_pages:
            data = self.call("GET", f"/repos/{repo}/labels?per_page=100&page={page}", None)
            if not isinstance(data, list) or not data:
                break
            names.extend([x.get("name", "") for x in data if isinstance(x, dict)])
            if len(data) < 100:
                break
            page += 1
        return [n for n in names if n]

    def create_labels_if_needed(self, repo: str, labels: List[str]) -> None:
        """E2: POST-first strategy — try to create, treat 422/409 as 'already exists'.
        Eliminates the GET /labels pagination entirely."""
        for lbl in labels:
            try:
                self.call("POST", f"/repos/{repo}/labels", {"name": lbl})
                logging.info("Created label %s in %s", lbl, repo)
            except HTTPError as e:
                if e.code in (409, 422):
                    pass  # Label already exists — expected
                else:
                    logging.warning("Could not create label %s in %s (HTTP %d)", lbl, repo, e.code)

    def create_issue(self, repo: str, title: str, body: str, labels: Optional[List[str]] = None) -> dict:
        payload: Dict[str, Any] = {"title": title, "body": body}
        if labels:
            payload["labels"] = labels
        if self.dry_run:
            logging.info("DRY RUN: would create issue in %s title=%s labels=%s", repo, title, labels)
            return {"number": 0, "html_url": "(dry-run)"}
        try:
            result = self.call("POST", f"/repos/{repo}/issues", payload)
            logging.info("Successfully created issue in %s: %s", repo, result.get("html_url", ""))
            return result
        except HTTPError as e:
            logging.error("Failed to create issue in %s: HTTP %d - %s", repo, e.code, e.reason)
            raise

    def verify_issue_exists(self, repo: str, issue_number: int) -> bool:
        """Verify that a created issue actually exists by fetching it back."""
        if self.dry_run or issue_number <= 0:
            return self.dry_run
        try:
            data = self.call("GET", f"/repos/{repo}/issues/{issue_number}", None)
            if isinstance(data, dict) and data.get("number") == issue_number:
                logging.info("Verified issue #%d exists in %s (state=%s)", issue_number, repo, data.get("state", "?"))
                return True
            logging.warning("Verification failed for issue #%d in %s: unexpected response", issue_number, repo)
            return False
        except HTTPError as e:
            logging.error("Verification failed for issue #%d in %s: HTTP %d", issue_number, repo, e.code)
            return False

    def create_suppression_labels(self, repo: str) -> None:
        """Create the suppression labels (with colors and descriptions) in the target repo.
        Uses POST-first strategy; 422/409 means label already exists."""
        for label_name, props in SUPPRESSION_LABELS.items():
            try:
                self.call("POST", f"/repos/{repo}/labels", {
                    "name": label_name,
                    "color": props["color"],
                    "description": props["description"],
                })
                logging.info("Created suppression label %s in %s", label_name, repo)
            except HTTPError as e:
                if e.code in (409, 422):
                    pass  # Already exists
                else:
                    logging.warning("Could not create suppression label %s in %s (HTTP %d)", label_name, repo, e.code)

    def get_suppressed_hashes(self, repo: str, title_prefix: str = "[TruffleHog]") -> set:
        """Fetch findings hashes from issues that have suppression labels.

        Returns a set of hash strings. If the current scan's findings hash
        is in this set, the scanner should skip issue creation for that repo.

        Searches issues labeled with any of SUPPRESSION_LABEL_NAMES, extracts
        the 8-char hex hash from the title pattern: [TruffleHog] Secrets scan report (HASH)
        """
        suppressed: set = set()
        hash_re = re.compile(r'\(([a-f0-9]{8})\)\s*$')

        for label in SUPPRESSION_LABEL_NAMES:
            try:
                encoded_label = urlquote(label, safe='')
                for page in [1, 2]:
                    data = self.call(
                        "GET",
                        f"/repos/{repo}/issues?state=all&labels={encoded_label}&per_page=30&page={page}",
                        None,
                    )
                    if not isinstance(data, list):
                        break
                    for issue in data:
                        if not isinstance(issue, dict):
                            continue
                        title = issue.get("title", "")
                        if title_prefix not in title:
                            continue
                        m = hash_re.search(title)
                        if m:
                            suppressed.add(m.group(1))
                    if len(data) < 30:
                        break
            except HTTPError as e:
                logging.warning("Failed to check suppression label %s in %s: HTTP %d", label, repo, e.code)

        if suppressed:
            logging.info("Found %d suppressed finding hash(es) in %s: %s", len(suppressed), repo, suppressed)
        return suppressed

    def process_comment_commands(self, repo: str, title_prefix: str = "[TruffleHog]") -> int:
        """Scan open TruffleHog issues for /trufflehog comment commands.

        When a comment contains a recognized command (e.g., '/trufflehog false-positive'),
        the corresponding label is applied to the issue automatically. This allows teams
        to suppress findings via comments without needing the label sidebar.

        Returns the number of labels applied.
        """
        if self.dry_run:
            logging.info("DRY RUN: would process comment commands in %s", repo)
            return 0

        applied = 0
        try:
            for page in [1, 2]:
                data = self.call(
                    "GET",
                    f"/repos/{repo}/issues?state=open&per_page=30&page={page}",
                    None,
                )
                if not isinstance(data, list):
                    break
                for issue in data:
                    if not isinstance(issue, dict):
                        continue
                    title = issue.get("title", "")
                    if title_prefix not in title:
                        continue

                    # Get existing labels on this issue
                    existing_labels = set()
                    for lbl in issue.get("labels", []):
                        if isinstance(lbl, dict):
                            existing_labels.add(lbl.get("name", ""))

                    issue_number = issue.get("number")
                    if not issue_number:
                        continue

                    # Fetch comments for this issue
                    try:
                        comments = self.call(
                            "GET",
                            f"/repos/{repo}/issues/{issue_number}/comments?per_page=100",
                            None,
                        )
                        if not isinstance(comments, list):
                            continue

                        for comment in comments:
                            if not isinstance(comment, dict):
                                continue
                            body = (comment.get("body") or "").strip().lower()
                            for cmd, label in COMMENT_COMMANDS.items():
                                if body.startswith(cmd) and label not in existing_labels:
                                    try:
                                        self.call(
                                            "POST",
                                            f"/repos/{repo}/issues/{issue_number}/labels",
                                            {"labels": [label]},
                                        )
                                        logging.info("Applied label %s to %s#%d via comment command", label, repo, issue_number)
                                        existing_labels.add(label)
                                        applied += 1
                                    except HTTPError as e:
                                        logging.warning("Failed to apply label %s to %s#%d: HTTP %d", label, repo, issue_number, e.code)
                    except HTTPError as e:
                        logging.warning("Failed to fetch comments for %s#%d: HTTP %d", repo, issue_number, e.code)

                if len(data) < 30:
                    break
        except HTTPError as e:
            logging.warning("Failed to list issues in %s for comment commands: HTTP %d", repo, e.code)

        if applied > 0:
            logging.info("Applied %d label(s) from comment commands in %s", applied, repo)
        return applied

# ---------------------------- Finding processing ----------------------------

def make_finding_key(repo: str, detector: str, path: str,
                     line: Optional[int], commit: Optional[str]) -> Tuple[str, str, str, int, str]:
    """
    Stable key to identify a unique finding instance.

    Converts None values to canonical forms to ensure proper deduplication:
    - None line numbers become -1 (unknown line)
    - None commits become empty string
    """
    canonical_line = line if line is not None else -1
    canonical_commit = str(commit) if commit else ""
    return (sanitize_repo(repo or ""), str(detector or ""), str(path or ""), canonical_line, canonical_commit)

def iter_ndjson(path: str, verified_only: bool = False, exclude_regexes: Optional[List[re.Pattern]] = None) -> Generator[Dict[str, Any], None, None]:
    """Generator that yields parsed NDJSON objects, optionally filtered."""
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return
    exclude_regexes = exclude_regexes or []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if verified_only and obj.get("Verified") is not True:
                    continue
                if exclude_regexes and is_excluded(obj, exclude_regexes):
                    continue
                yield obj
            except Exception:
                continue

def load_findings(ndjson_path: str, exclude_regexes: List[re.Pattern], include_unverified: bool = False, allow_no_repo: bool = False) -> Dict[str, List[Dict[str, Any]]]:
    """Read NDJSON and return findings grouped by repo, filtered to Verified==True (or all if include_unverified) and not excluded, de-duped.
    
    If allow_no_repo is True, findings without repo metadata are grouped under '_filesystem_' (useful for filesystem scans with --target-repo).
    Now includes 'redacted' and 'verified' fields for richer issue content (I3, I4).
    """
    findings_by_repo: Dict[str, List[Dict[str, Any]]] = {}
    seen = set()

    verified_only = not include_unverified
    for obj in iter_ndjson(ndjson_path, verified_only=verified_only, exclude_regexes=exclude_regexes):
        repo = sanitize_repo(find_repo(obj) or "")
        if not repo:
            if allow_no_repo:
                repo = "_filesystem_"
            else:
                continue

        item = {
            "detector": obj.get("DetectorName") or obj.get("DetectorType") or "Unknown",
            "file": file_path(obj),
            "line": line_no(obj),
            "commit": commit_sha(obj),
            "redacted": obj.get("Redacted") or "",
            "verified": obj.get("Verified", False),
        }

        key = make_finding_key(repo, item["detector"], item["file"], item["line"], item["commit"])
        if key not in seen:
            seen.add(key)
            findings_by_repo.setdefault(repo, []).append(item)

    mode = "verified and not excluded" if verified_only else "all (including unverified), not excluded"
    logging.info("Loaded findings for %d repo(s) (%s)", len(findings_by_repo), mode)
    return findings_by_repo

# ---------------------------- Diagnostics and summary ----------------------------

def _print_one_sanitized(o: Dict[str, Any], org: str) -> None:
    det = o.get("DetectorName") or o.get("DetectorType") or "Unknown"
    repo = sanitize_repo(find_repo(o) or "")
    fpath = file_path(o)
    ln = line_no(o)
    ver = o.get("Verified")
    sha = commit_sha(o)
    link = line_link(repo, sha, fpath, ln)
    ln_str = str(ln) if ln is not None else ""
    if link:
        print(f"[{org}] {repo} | {det} | {shortpath(fpath)}:{ln_str} | verified={ver} | {link}")
    else:
        print(f"[{org}] {repo} | {det} | {shortpath(fpath)}:{ln_str} | verified={ver}")

def print_sanitized_preview(ndjson_path: str, org: str, max_lines: int,
                            exclude_regexes: List[re.Pattern]) -> None:
    """Print up to N sanitized VERIFIED findings to stdout for quick inspection."""
    if max_lines <= 0:
        return
    shown = 0
    for obj in iter_ndjson(ndjson_path, verified_only=True, exclude_regexes=exclude_regexes):
        if shown >= max_lines:
            break
        _print_one_sanitized(obj, org)
        shown += 1
    if shown > 0:
        logging.info("[%s] preview printed %d line(s)", org, shown)

def ndjson_stats(path: str, exclude_regexes: List[re.Pattern], org: str) -> Tuple[int, int, int, int]:
    total, bad, repos, dets = 0, 0, set(), set()
    if not os.path.exists(path):
        logging.info("[%s] NDJSON not found: %s", org, path)
        return (0, 0, 0, 0)

    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            total += 1
            try:
                o = json.loads(raw)
                if not is_excluded(o, exclude_regexes):
                    if r := find_repo(o):
                        repos.add(sanitize_repo(r))
                    if d := (o.get("DetectorName") or o.get("DetectorType")):
                        dets.add(str(d))
            except Exception:
                bad += 1
    return (total, bad, len(repos), len(dets))

def load_errors_ndjson(path: Optional[str]) -> List[Dict[str, Any]]:
    errs: List[Dict[str, Any]] = []
    if not path or not os.path.exists(path) or os.path.getsize(path) == 0:
        return errs
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
                if isinstance(o, dict):
                    errs.append(o)
            except Exception:
                continue
    return errs

def write_markdown_summary(ndjson_path: str,
                           org: str,
                           exclude_regexes: List[re.Pattern],
                           include_detailed: bool = False,
                           detailed_per_repo: int = 10,
                           detailed_max_total: int = 300,
                           errors_path: Optional[str] = None,
                           target_repo: Optional[str] = None,
                           created_count: Optional[int] = None,
                           skipped_count: Optional[int] = None,
                           failed_repos: Optional[List[str]] = None) -> None:
    """
    Build a verified-only summary with strict de-duplication:
    uniqueness key = (repo, detector, file, line, commit).
    Counts and detailed rows reflect unique findings only.

    Improvements (S1/S2/S5):
    - Severity breakdown table using DETECTOR_SEVERITY mapping
    - False-positive / unverified filter statistics
    - Markdown [View](url) links in detailed table
    - Top offenders callout for repos with >= 5 findings
    """
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")

    # ---- tracking structures for verified AND unverified ----
    seen_verified: set = set()
    seen_unverified: set = set()

    # Verified counters
    ver_repo = collections.Counter()
    ver_det = collections.Counter()
    ver_sev = collections.Counter()
    ver_total = 0

    # Unverified counters (new: full breakdown instead of single int)
    unver_repo = collections.Counter()
    unver_det = collections.Counter()
    unver_sev = collections.Counter()
    unver_repo_det: collections.Counter = collections.Counter()  # (repo, detector) pairs
    unver_total = 0

    # Detailed rows (verified + unverified)
    # (detector, severity, file, line, view_link, blame_link)
    ver_repo_rows: Dict[str, List[Tuple[str, str, str, Optional[int], str, str]]] = {}
    unver_repo_rows: Dict[str, List[Tuple[str, str, str, Optional[int], str, str]]] = {}

    # S2: filter statistics
    total_lines = 0
    total_excluded = 0
    total_bad = 0

    if os.path.exists(ndjson_path) and os.path.getsize(ndjson_path) > 0:
        with open(ndjson_path, "r", encoding="utf-8") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                total_lines += 1
                try:
                    o = json.loads(raw)
                except Exception:
                    total_bad += 1
                    continue

                # Check exclusion first
                if exclude_regexes and is_excluded(o, exclude_regexes):
                    total_excluded += 1
                    continue

                det = o.get("DetectorName") or o.get("DetectorType") or "Unknown"
                repo = sanitize_repo(find_repo(o) or "")
                if not repo:
                    if target_repo:
                        repo = sanitize_repo(target_repo)
                    else:
                        continue

                fpath = file_path(o)
                ln = line_no(o)
                sha = commit_sha(o)
                link = line_link(repo, sha, fpath, ln)
                bl_link = blame_link(repo, sha, fpath, ln)
                norm_det = normalize_detector(det)
                sev = DETECTOR_SEVERITY.get(norm_det, "sec:medium")
                is_verified = o.get("Verified") is True

                if is_verified:
                    key = make_finding_key(repo, det, fpath, ln, sha)
                    if key in seen_verified:
                        continue
                    seen_verified.add(key)
                    ver_total += 1
                    ver_repo[repo] += 1
                    ver_det[det] += 1
                    ver_sev[sev] += 1
                    if include_detailed:
                        ver_repo_rows.setdefault(repo, []).append((det, sev, fpath, ln, link, bl_link))
                else:
                    key = make_finding_key(repo, det, fpath, ln, sha)
                    if key in seen_unverified:
                        continue
                    seen_unverified.add(key)
                    unver_total += 1
                    unver_repo[repo] += 1
                    unver_det[det] += 1
                    unver_sev[sev] += 1
                    unver_repo_det[(repo, det)] += 1
                    if include_detailed:
                        unver_repo_rows.setdefault(repo, []).append((det, sev, fpath, ln, link, bl_link))

    # ---- Build summary markdown ----
    grand_total = ver_total + unver_total
    ver_repo_count = len(ver_repo)  # repos with at least one verified finding
    content_lines: List[str] = []
    content_lines.append(f"## TruffleHog summary for `{org}`\n")
    content_lines.append(f"| Metric | Count |\n|---|---:|\n")
    content_lines.append(f"| Total unique findings | **{grand_total}** |\n")
    if created_count is not None:
        content_lines.append(f"| Verified findings | **{ver_total}** |\n")
        content_lines.append(f"| Repos with verified findings | **{ver_repo_count}** |\n")
        content_lines.append(f"| Issues created | **{created_count}** |\n")
        if skipped_count:
            content_lines.append(f"| :white_check_mark: Skipped (pre-existing issue) | **{skipped_count}** |\n")
        if failed_repos:
            content_lines.append(f"| :x: Repos with issue creation failures | **{len(failed_repos)}** |\n")
        # Gap check: warn only when repos are unaccounted for (created + skipped + failed < repos with findings)
        accounted = created_count + (skipped_count or 0) + len(failed_repos or [])
        if ver_repo_count > accounted:
            content_lines.append(f"| :warning: Repos unaccounted for | **{ver_repo_count - accounted}** |\n")
    else:
        content_lines.append(f"| Verified findings (issues pending) | **{ver_total}** |\n")
    content_lines.append(f"| Unverified (informational) | **{unver_total}** |\n")
    if total_excluded > 0:
        content_lines.append(f"| Filtered as false-positive | **{total_excluded}** |\n")
    if total_bad > 0:
        content_lines.append(f"| Malformed NDJSON lines | **{total_bad}** |\n")
    content_lines.append("\n")

    # Severity breakdown — combined verified + unverified
    sev_emoji = {"sec:critical": "🔴", "sec:high": "🟠", "sec:medium": "🟡", "sec:low": "🟢"}
    any_sev = any(ver_sev.get(s, 0) + unver_sev.get(s, 0) > 0 for s in SEVERITY_ORDER)
    if any_sev:
        content_lines.append("### Findings by severity\n")
        content_lines.append("| Severity | Verified | Unverified | Total |\n|---|---:|---:|---:|\n")
        for sev in SEVERITY_ORDER[::-1]:  # critical first
            v = ver_sev.get(sev, 0)
            u = unver_sev.get(sev, 0)
            if v + u > 0:
                emoji = sev_emoji.get(sev, "")
                sev_label = sev.replace("sec:", "").upper()
                content_lines.append(f"| {emoji} {sev_label} | {v} | {u} | {v + u} |\n")
        content_lines.append("\n")

    # Top offenders callout (verified >= 5 or combined >= 10)
    all_repos = set(list(ver_repo.keys()) + list(unver_repo.keys()))
    top_offenders = [(r, ver_repo.get(r, 0), unver_repo.get(r, 0))
                     for r in all_repos
                     if ver_repo.get(r, 0) >= 5 or (ver_repo.get(r, 0) + unver_repo.get(r, 0)) >= 10]
    top_offenders.sort(key=lambda x: x[1] + x[2], reverse=True)
    if top_offenders:
        content_lines.append("### Top offenders\n")
        content_lines.append("| Repository | Verified | Unverified | Total |\n|---|---:|---:|---:|\n")
        for r, v, u in top_offenders:
            content_lines.append(f"| `{r}` | {v} | {u} | {v + u} |\n")
        content_lines.append("\n")

    # Findings by repository — combined table (top 50 by total, rest in collapsible)
    if all_repos:
        content_lines.append("### Findings by repository\n")
        content_lines.append("| Repository | Verified | Unverified | Total |\n|---|---:|---:|---:|\n")
        combined_repo = [(r, ver_repo.get(r, 0), unver_repo.get(r, 0)) for r in all_repos]
        combined_repo.sort(key=lambda x: x[1] + x[2], reverse=True)
        shown = combined_repo[:50]
        rest = combined_repo[50:]
        for r, v, u in shown:
            content_lines.append(f"| `{r}` | {v} | {u} | {v + u} |\n")
        if rest:
            content_lines.append("\n<details>\n<summary>Show remaining %d repositories</summary>\n\n" % len(rest))
            content_lines.append("| Repository | Verified | Unverified | Total |\n|---|---:|---:|---:|\n")
            for r, v, u in rest:
                content_lines.append(f"| `{r}` | {v} | {u} | {v + u} |\n")
            content_lines.append("\n</details>\n")
        content_lines.append("\n")

    # Findings by detector — combined table
    all_dets = set(list(ver_det.keys()) + list(unver_det.keys()))
    if all_dets:
        content_lines.append("### Findings by detector\n")
        content_lines.append("| Detector | Verified | Unverified | Total |\n|---|---:|---:|---:|\n")
        combined_det = [(d, ver_det.get(d, 0), unver_det.get(d, 0)) for d in all_dets]
        combined_det.sort(key=lambda x: x[1] + x[2], reverse=True)
        for d, v, u in combined_det:
            content_lines.append(f"| {d} | {v} | {u} | {v + u} |\n")
        content_lines.append("\n")

    # Unverified breakdown: top repo × detector combinations
    if unver_repo_det:
        content_lines.append("### Unverified findings by repo × detector (top 50)\n")
        content_lines.append("| Repository | Detector | Count |\n|---|---|---:|\n")
        top_combos = unver_repo_det.most_common(50)
        for (r, d), cnt in top_combos:
            content_lines.append(f"| `{r}` | {d} | {cnt} |\n")
        omitted = len(unver_repo_det) - len(top_combos)
        if omitted > 0:
            content_lines.append(f"\n_... {omitted} more repo×detector combinations omitted._\n")
        content_lines.append("\n")

    # Detailed verified findings
    if include_detailed and ver_repo_rows:
        content_lines.append("### Detailed verified findings (issues created)\n")
        total_rows = 0
        for repo in sorted(ver_repo_rows, key=lambda r: ver_repo.get(r, 0), reverse=True):
            rows = list(dict.fromkeys(ver_repo_rows[repo]))
            if not rows or total_rows >= max(1, detailed_max_total):
                continue
            content_lines.append(f"#### `{repo}`\n")
            content_lines.append("| Severity | Detector | File | Line | View | Blame |\n|---|---|---|---:|---|---|\n")
            for idx, (det, sev, fpath, ln, link, bl) in enumerate(rows):
                if idx >= max(1, detailed_per_repo) or total_rows >= max(1, detailed_max_total):
                    break
                ln_str = str(ln) if ln is not None else ""
                link_display = f"[View]({link})" if link else ""
                blame_display = f"[Blame]({bl})" if bl else ""
                sev_label = sev.replace("sec:", "").upper()
                content_lines.append(f"| {sev_label} | {det} | `{fpath}` | {ln_str} | {link_display} | {blame_display} |\n")
                total_rows += 1
            content_lines.append("\n")
        if total_rows >= max(1, detailed_max_total):
            remaining = sum(len(v) for v in ver_repo_rows.values()) - total_rows
            if remaining > 0:
                content_lines.append(f"_... {remaining} more verified finding(s) omitted._\n\n")

    # Detailed unverified findings (informational — no issues created)
    if include_detailed and unver_repo_rows:
        content_lines.append("### Unverified findings (informational — no issues created)\n")
        content_lines.append("<details>\n<summary>Expand to view unverified findings</summary>\n\n")
        total_rows = 0
        for repo in sorted(unver_repo_rows, key=lambda r: unver_repo.get(r, 0), reverse=True):
            rows = list(dict.fromkeys(unver_repo_rows[repo]))
            if not rows or total_rows >= max(1, detailed_max_total):
                continue
            content_lines.append(f"#### `{repo}`\n")
            content_lines.append("| Severity | Detector | File | Line | View | Blame |\n|---|---|---|---:|---|---|\n")
            for idx, (det, sev, fpath, ln, link, bl) in enumerate(rows):
                if idx >= max(1, detailed_per_repo) or total_rows >= max(1, detailed_max_total):
                    break
                ln_str = str(ln) if ln is not None else ""
                link_display = f"[View]({link})" if link else ""
                blame_display = f"[Blame]({bl})" if bl else ""
                sev_label = sev.replace("sec:", "").upper()
                content_lines.append(f"| {sev_label} | {det} | `{fpath}` | {ln_str} | {link_display} | {blame_display} |\n")
                total_rows += 1
            content_lines.append("\n")
        if total_rows >= max(1, detailed_max_total):
            remaining = sum(len(v) for v in unver_repo_rows.values()) - total_rows
            if remaining > 0:
                content_lines.append(f"_... {remaining} more unverified finding(s) omitted._\n\n")
        content_lines.append("</details>\n\n")

    errs = load_errors_ndjson(errors_path)
    if errs:
        content_lines.append("### API/Scan errors observed\n")
        content_lines.append("| Repo | Stage | Status | Message |\n|---|---|---:|---|\n")
        for e in errs:
            content_lines.append(f"| `{e.get('repo','')}` | {e.get('stage','')} | {e.get('status','')} | {str(e.get('message','')).replace('|','&#124;')} |\n")
        content_lines.append("\n")

    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as out:
            out.writelines(content_lines)
        print(f"[{org}] summary written (verified={ver_total}, unverified={unver_total}, excluded={total_excluded})")
    else:
        print("".join(content_lines))

# ---------------------------- Issue creation ----------------------------

def compute_findings_hash(items: List[Dict[str, Any]]) -> str:
    """Deterministic 8-char hex hash of a set of findings.
    
    The hash is derived from a sorted, canonical representation of all
    finding keys (repo, detector, file, line, commit). This means:
    - Same findings in different order produce the same hash.
    - Adding or removing a finding changes the hash.
    - The hash appears in the issue title for deduplication.
    """
    # Create a stable, sorted representation of all findings
    stable_items = []
    for item in items:
        # Use key characteristics: file, line, commit
        stable_items.append((
            item.get("file", ""),
            item.get("line", -1),
            item.get("commit", "")
        ))

    # Sort to ensure deterministic ordering
    stable_items.sort()

    # Hash the representation
    hasher = hashlib.sha256()
    for file, line, commit in stable_items:
        hasher.update(f"{file}:{line}:{commit}".encode("utf-8"))

    # Return first 8 characters (short but unique enough)
    return hasher.hexdigest()[:8]

def repo_is_actionable(meta: Optional[dict]) -> bool:
    """Return True if the repo is accessible and not archived/disabled.
    
    We skip repos that can't receive issues -- archived repos reject
    issue creation with 422, and disabled repos are inaccessible.
    """
    if not meta:
        return False
    if meta.get("archived"):
        logging.info("Repo archived, skipping")
        return False
    if not meta.get("has_issues", True):
        logging.info("Issues disabled, skipping")
        return False
    return True

def build_issue_body(repo: str, items: List[Dict[str, Any]], run_url: str, scanner_repo: str = "",
                     findings_hash: str = "") -> str:
    """Build the GitHub issue body with severity badge (I1), commit SHA (I2),
    redacted preview (I3), verified status (I4), detector-specific remediation (I5),
    FP suppression guide (I6), scan timestamp (I7), and hash explanation (I8)."""
    count_text = "1 verified secret" if len(items) == 1 else f"{len(items)} verified secrets"
    # Adjust text if using --include-unverified (items may be unverified)
    has_unverified = any(not it.get("verified", True) for it in items)
    if has_unverified or (len(items) > 0 and items[0].get("original_repo")):
        count_text = "1 finding" if len(items) == 1 else f"{len(items)} findings"

    # I1: Severity badge at top
    sev = highest_severity(items)
    sev_label = sev.replace("sec:", "").upper()
    sev_emoji = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🟢"}.get(sev_label, "⚪")

    # I7: Scan timestamp
    scan_time = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    header = [
        f"## {sev_emoji} Secret Detection Alert — Severity: {sev_label}",
        "",
        f"**Status**: {count_text} found in this repository",
        f"**Severity**: {sev_emoji} **{sev_label}**",
        f"**Scan date**: {scan_time}",
        f"**Scan**: [View workflow run]({run_url})" if run_url else "",
    ]

    # I8: Findings hash explanation
    if findings_hash:
        header.append(f"**Findings hash**: `{findings_hash}` _(used for deduplication — same secrets produce the same hash)_")

    header.extend([
        "",
        "> **SECURITY NOTICE**: Do not paste secret values in this issue. All secrets below should be considered compromised.",
        "",
        "### Findings",
        "",
        "| Detector | File | Line | Commit | Verified | Redacted | View | Blame |",
        "|---|---|---:|---|---|---|---|---|",
    ])
    rows = []
    # Collect unique detector types for targeted remediation
    seen_detectors: set = set()
    for it in items:
        path = it.get("file", "") or ""
        ln = it.get("line")
        sha = it.get("commit")
        # Use original_repo for link if available (consolidation mode)
        # Skip _filesystem_ placeholder — it's not a valid GitHub repo
        orig = it.get("original_repo") or ""
        link_repo = orig if orig and orig != "_filesystem_" else repo
        link_url = line_link(link_repo, sha, path, ln)
        bl_url = blame_link(link_repo, sha, path, ln)
        ln_str = str(ln) if ln is not None else ""

        # I2: Short commit SHA
        sha_display = f"`{sha[:7]}`" if sha else ""

        # I3: Redacted preview
        redacted = it.get("redacted", "") or ""
        redacted_display = f"`{redacted[:40]}`" if redacted else ""

        # I4: Verified status
        ver_display = "✅" if it.get("verified", False) else "❓"

        # Format link as markdown to make it clickable
        link_display = f"[View]({link_url})" if link_url else ""
        blame_display = f"[Blame]({bl_url})" if bl_url else ""

        det_name = it.get('detector', '')
        rows.append(f"| {det_name} | `{path}` | {ln_str} | {sha_display} | {ver_display} | {redacted_display} | {link_display} | {blame_display} |")
        seen_detectors.add(normalize_detector(det_name))

    # Build remediation guide link if scanner_repo is provided
    remediation_link = ""
    if scanner_repo:
        remediation_link = f"https://github.com/{scanner_repo}/blob/main/README.md#remediating-discovered-secrets"

    guidance = [
        "",
        "---",
        "",
        "## Remediation Steps",
        "",
        "### Step 1: Rotate/Revoke Immediately",
        "**Assume all secrets above are compromised.**",
        "",
    ]

    # I5: Detector-specific remediation
    specific_guidance = []
    for det in sorted(seen_detectors):
        if det in DETECTOR_REMEDIATION:
            specific_guidance.append(f"**{det}**:\n{DETECTOR_REMEDIATION[det]}")
    if specific_guidance:
        guidance.append("### Detector-Specific Rotation Steps")
        guidance.append("")
        for sg in specific_guidance:
            guidance.append(sg)
            guidance.append("")

    guidance.extend([
        "### Step 2: Remove from Git History",
        "**Recent commits** (last few commits):",
        "```bash",
        "git rebase -i HEAD~5  # Edit/drop commits containing secrets",
        "```",
        "",
        "**Older commits** (anywhere in history):",
        "```bash",
        "# Install git-filter-repo first: pip install git-filter-repo",
        "git filter-repo --replace-text <(echo 'YOUR_SECRET_HERE==>REDACTED')",
        "```",
        "",
        "### Step 3: Force Push & Notify Team",
        "```bash",
        "git push --force-with-lease",
        "```",
        "**WARNING**: Coordinate with your team before force-pushing to shared branches.",
        "",
    ])

    # Response Options section — label-based feedback loop
    guidance.extend([
        "---",
        "",
        "## 📨 Response Options",
        "",
        "Use **labels** or **comment commands** to tell the scanner how to handle this finding on future scans.",
        "Adding a suppression label prevents the scanner from re-creating this issue.",
        "",
        "### Quick Actions (add a label in the sidebar)",
        "",
        "| Label | When to Use | Effect |",
        "|---|---|---|",
        "| `trufflehog:false-positive` | Not a real secret (test data, example, docs) | 🚫 Suppressed on future scans |",
        "| `trufflehog:accepted-risk` | Real but team accepts the risk | 🚫 Suppressed on future scans |",
        "| `trufflehog:wont-fix` | Won't remediate (already revoked, low impact) | 🚫 Suppressed on future scans |",
        "| `trufflehog:remediated` | Secret rotated and removed from history | ✅ Informational (close issue) |",
        "",
        "### Comment Commands",
        "",
        "Or comment on this issue with one of these commands:",
        "",
        "```",
        "/trufflehog false-positive",
        "/trufflehog accepted-risk",
        "/trufflehog wont-fix",
        "/trufflehog remediated",
        "```",
        "",
        "The scanner will automatically apply the corresponding label on the next scan run.",
        "",
        "### Manual Suppression (regex allowlist)",
        "",
        "To permanently suppress this detector/file combination across all repos, add a regex",
        "to the exclude patterns file in the scanner repository:",
        "",
        "```",
        "# In .github/trufflehog/false_positives.txt, add a line like:",
        f"file={path}.*detector=.*  # Suppress findings in this file",
        "```",
        "",
        "See the [exclude patterns documentation](https://github.com/" + (scanner_repo or "your-org/trufflehog") + "/blob/main/README.md) for details.",
        "",
    ])

    # Always show additional resources
    resources = [
        "### Additional Resources",
        "- [Removing sensitive data from a repository](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository)",
        "- [GitHub secret scanning documentation](https://docs.github.com/en/code-security/secret-scanning)",
    ]

    if remediation_link:
        resources.insert(1, f"- [Complete remediation guide]({remediation_link})")

    resources.append("")
    guidance.extend(resources)

    return "\n".join([s for s in header if s != ""] + rows + guidance)

def process_repo(repo: str,
                 items: List[Dict[str, Any]],
                 gh: GHClient,
                 title_prefix: str,
                 run_url: str,
                 extra_labels: Optional[List[str]],
                 dry_run: bool,
                 scanner_repo: str = "",
                 force_create: bool = False) -> Tuple[str, Optional[bool]]:
    """Process findings for a single repo: dedup check, label setup, issue creation.
    
    This is the per-repo worker function called from a ThreadPoolExecutor.
    It performs the full lifecycle:
    1. Get an org-scoped token (GitHub App only)
    2. Verify the repo is accessible and not archived
    3. Process any pending /trufflehog comment commands on existing issues
    4. Compute a deterministic hash of the findings for deduplication
    5. Check suppression labels and existing issues (skippable with force_create)
    6. Create labels, then the issue itself
    7. Verify the issue actually exists via GET-back check
    
    Returns: (repo_name, result) where result is True (created), False (failed),
             or None (skipped/deduped -- not an error)
    """
    # Get an org-scoped token for the target repo's org (no-op for PAT auth)
    org = repo.split("/")[0] if "/" in repo else ""
    if org:
        gh = gh.for_org(org)
    logging.info("Processing repo %s with %d finding(s)", repo, len(items))
    meta = gh.repo_meta(repo)
    if not repo_is_actionable(meta):
        return (repo, None)

    # Process any /trufflehog comment commands on existing issues (applies labels)
    try:
        gh.process_comment_commands(repo, title_prefix)
    except Exception as e:
        logging.warning("Comment command processing failed for %s: %s", repo, e)

    # I9/E6: Compute unique hash; title uses hash only (no date) for cheaper dedup
    findings_hash = compute_findings_hash(items)
    title = f"{title_prefix} Secrets scan report ({findings_hash})"

    if not force_create:
        # Check suppression labels: if an existing issue with this hash has been
        # labeled false-positive/accepted-risk/wont-fix, skip issue creation
        suppressed_hashes = gh.get_suppressed_hashes(repo, title_prefix)
        if findings_hash in suppressed_hashes:
            logging.info("Finding hash %s is suppressed in %s (labeled false-positive/accepted-risk/wont-fix)",
                         findings_hash, repo)
            return (repo, None)

        # Primary: exact title match (hash-based, date-free) — 1-2 API calls
        if gh.search_issue_by_title(repo, title):
            logging.info("Issue already exists for %s with same findings (hash=%s)", repo, findings_hash)
            return (repo, None)

        # Secondary: search for issues with same hash within 90 days (2 pages max)
        title_pattern = f"{title_prefix} Secrets scan report"
        hash_pattern = f"({findings_hash})"
        if gh.search_recent_issues_with_hash(repo, title_pattern, hash_pattern, days=90):
            logging.info("Issue already exists in %s with same findings (hash=%s), skipping", repo, findings_hash)
            return (repo, None)
    else:
        logging.info("Force-create enabled, skipping deduplication checks")

    auto = labels_for_items(items, extra_labels)
    try:
        gh.create_labels_if_needed(repo, auto)
    except HTTPError as e:
        logging.warning("Skipping label creation in %s due to HTTP %d; continuing to issue body.", repo, e.code)

    # Create suppression labels so team can use them immediately
    try:
        gh.create_suppression_labels(repo)
    except Exception as e:
        logging.warning("Suppression label creation failed in %s: %s", repo, e)

    body = build_issue_body(repo, items, run_url, scanner_repo, findings_hash=findings_hash)
    try:
        created = gh.create_issue(repo, title, body, auto) or {}
        issue_number = created.get("number")
        if issue_number and isinstance(issue_number, int) and issue_number > 0:
            # Post-creation verification: confirm the issue actually exists
            if gh.verify_issue_exists(repo, issue_number):
                logging.info("Created and verified issue in %s number=%s", repo, issue_number)
                return (repo, True)
            else:
                logging.error("Issue #%d was reportedly created in %s but verification FAILED", issue_number, repo)
                return (repo, False)
        elif gh.dry_run:
            logging.info("DRY RUN: would create issue in %s", repo)
            return (repo, True)
        else:
            logging.error("Issue creation in %s returned no valid issue number: %s", repo, created)
            return (repo, False)
    except HTTPError as e:
        logging.warning("Failed to create issue in %s: HTTP %d", repo, e.code)
        return (repo, False)

# ---------------------------- Diagnostics helpers ----------------------------

def diagnose_findings_dir(diag_dir: str, org: str, max_files: int, max_lines: int,
                          exclude_regexes: Optional[List[re.Pattern]] = None) -> None:
    """Print quick diagnostics for per-repo NDJSON files."""
    if not diag_dir or not os.path.isdir(diag_dir):
        logging.info("[%s] diag dir not found: %s", org, diag_dir)
        return

    printed = 0
    for root, _dirs, files in os.walk(diag_dir):
        for fn in sorted(files):
            if not fn.endswith(".ndjson") or printed >= max_files:
                continue
            path = os.path.join(root, fn)
            print(f"[{org}] diag file: {path} bytes={os.path.getsize(path)}")

            c = 0
            for obj in iter_ndjson(path):
                if c >= max_lines:
                    break
                _print_one_sanitized(obj, org)
                c += 1
            printed += 1

# ---------------------------- CLI and main ----------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--ndjson", default="findings.ndjson")
    p.add_argument("--run-url", default="")
    p.add_argument("--scanner-repo", default="", help="GitHub repository with remediation docs (org/repo)")
    p.add_argument("--max-workers", type=int, default=0)
    p.add_argument("--labels", default="")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--title-prefix", default="[TruffleHog]")
    p.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "INFO"))
    p.add_argument("--print-sanitized", type=int, default=_safe_int_env("MAX_FINDING_LOG_LINES", 0))
    p.add_argument("--write-summary", action="store_true")
    p.add_argument("--org", default=os.environ.get("ORG", ""))

    # Diagnostics
    p.add_argument("--diag-dir", default="")
    p.add_argument("--diag-max-files", type=int, default=25)
    p.add_argument("--diag-max-lines", type=int, default=3)
    p.add_argument("--validate", action="store_true")

    # False positives
    p.add_argument("--exclude-patterns-file", default=None)

    # Summary details
    p.add_argument("--summary-detailed", action="store_true")
    p.add_argument("--summary-detailed-per-repo", type=int, default=10)
    p.add_argument("--summary-detailed-max", type=int, default=300)

    # Errors NDJSON (optional)
    p.add_argument("--errors-ndjson", default=None)

    # Testing flags
    p.add_argument("--include-unverified", action="store_true",
                   help="Include unverified findings for issue creation (for testing only)")
    p.add_argument("--target-repo", default=None,
                   help="Override target repo for issue creation (for self-test, e.g. 'org/repo')")
    p.add_argument("--force-create", action="store_true",
                   help="Skip deduplication checks and always create issues (for testing only)")
    return p.parse_args()

def main() -> int:
    """Entry point: parse args, load findings, create issues, write summary.
    
    Execution flow:
    1. Load false-positive regex patterns
    2. Run optional diagnostics (--diag-dir) and preview (--print-sanitized)
    3. If --validate: print stats and exit
    4. Authenticate (GitHub App > PAT > GITHUB_TOKEN)
    5. Load verified findings from NDJSON, grouped by repo
    6. Dispatch process_repo() via ThreadPoolExecutor (one thread per repo)
    7. Write summary to GITHUB_STEP_SUMMARY with actual created/failed counts
    8. Exit 0 on success, 2 if any issue creation failed
    """
    args = parse_args()
    setup_logging(args.log_level)

    # Load FP regexes
    exclude_regexes = load_exclude_regexes(args.exclude_patterns_file)

    # Diagnostics for per-repo files (raw, not filtered)
    if args.diag_dir:
        try:
            diagnose_findings_dir(args.diag_dir, args.org, args.diag_max_files, args.diag_max_lines)
        except Exception as e:
            logging.warning("diagnose_findings_dir failed: %s", e)

    # Optional preview for merged NDJSON (filtered by exclude patterns + Verified)
    if os.path.exists(args.ndjson):
        try:
            print_sanitized_preview(args.ndjson, args.org, args.print_sanitized, exclude_regexes)
        except Exception as e:
            logging.warning("Sanitized preview failed: %s", e)

    # Read-only validation stats
    if args.validate:
        try:
            total, bad, repo_cnt, det_cnt = ndjson_stats(args.ndjson, exclude_regexes, args.org)
            logging.info("[%s] stats: lines=%d bad=%d unique_repos=%d unique_detectors=%d",
                         args.org, total, bad, repo_cnt, det_cnt)
        except Exception as e:
            logging.warning("validate stats failed: %s", e)
        return 0

    # If this invocation is clearly diagnostics-only (no summary requested and no NDJSON), exit quietly.
    if args.diag_dir and not args.write_summary and not os.path.exists(args.ndjson):
        logging.info("[%s] diagnostics-only run; no merged NDJSON to process", args.org)
        return 0

    # Summary is written AFTER issue creation so it can report actual
    # created_count.  Early-exit paths (no auth, no findings) write
    # their own summary before returning.

    try:
        token = get_auth_token()
    except ValueError as e:
        logging.info("Skipping issue creation: %s", e)
        # Still write summary (without issue counts) when auth is unavailable
        if os.path.exists(args.ndjson) and args.write_summary:
            try:
                write_markdown_summary(args.ndjson, args.org, exclude_regexes,
                    include_detailed=args.summary_detailed,
                    detailed_per_repo=max(1, args.summary_detailed_per_repo),
                    detailed_max_total=max(1, args.summary_detailed_max),
                    errors_path=args.errors_ndjson, target_repo=args.target_repo)
            except Exception as se:
                logging.warning("Summary generation failed: %s", se)
        return 0

    # Allow findings without repo metadata when --target-repo is specified (e.g., filesystem scans)
    allow_no_repo = bool(args.target_repo)
    findings = load_findings(args.ndjson, exclude_regexes, include_unverified=args.include_unverified, allow_no_repo=allow_no_repo)
    if not findings:
        if args.include_unverified:
            logging.info("No findings detected after filtering; no issues created")
        else:
            logging.info("No verified findings detected after filtering; no issues created")
        # Still write summary when there are no actionable findings
        if os.path.exists(args.ndjson) and args.write_summary:
            try:
                write_markdown_summary(args.ndjson, args.org, exclude_regexes,
                    include_detailed=args.summary_detailed,
                    detailed_per_repo=max(1, args.summary_detailed_per_repo),
                    detailed_max_total=max(1, args.summary_detailed_max),
                    errors_path=args.errors_ndjson, target_repo=args.target_repo)
            except Exception as se:
                logging.warning("Summary generation failed: %s", se)
        return 0

    app_id = os.environ.get('GH_APP_ID', '')
    app_private_key = os.environ.get('GH_APP_PRIVATE_KEY', '')
    gh = GHClient(token=token, dry_run=args.dry_run, app_id=app_id, app_private_key=app_private_key)
    extra_labels = [s.strip() for s in args.labels.split(",") if s.strip()] if args.labels else None
    max_workers = args.max_workers or max(1, min(8, (os.cpu_count() or 2) * 2))

    # If --target-repo is set, consolidate all findings into a single issue for that repo
    if args.target_repo:
        all_items = []
        for repo, items in findings.items():
            # Annotate each item with its original repo for the issue body
            for item in items:
                item["original_repo"] = repo
            all_items.extend(items)
        findings = {args.target_repo: all_items}
        logging.info("Using target-repo override: %s (consolidated %d findings)", args.target_repo, len(all_items))

    created_count = 0
    skipped_count = 0
    failed_repos: List[str] = []
    with cf.ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="issue") as pool:
        futures = {}
        for repo, items in findings.items():
            fut = pool.submit(
                process_repo, repo, items, gh, args.title_prefix, args.run_url, extra_labels, args.dry_run, args.scanner_repo, args.force_create
            )
            futures[fut] = repo
        for fut in cf.as_completed(futures):
            try:
                repo, result = fut.result()
                if result is True:
                    created_count += 1
                elif result is None:
                    skipped_count += 1
                else:
                    failed_repos.append(repo)
            except Exception as e:
                failed_repos.append(futures[fut])
                logging.error("Worker failed for %s: %s", futures[fut], e)

    logging.info("Issues created: %d, skipped/deduped: %d, failed: %d", created_count, skipped_count, len(failed_repos))

    # Write summary AFTER issue creation so we can report actual results
    if os.path.exists(args.ndjson) and args.write_summary:
        try:
            write_markdown_summary(
                args.ndjson,
                args.org,
                exclude_regexes,
                include_detailed=args.summary_detailed,
                detailed_per_repo=max(1, args.summary_detailed_per_repo),
                detailed_max_total=max(1, args.summary_detailed_max),
                errors_path=args.errors_ndjson,
                target_repo=args.target_repo,
                created_count=created_count if not args.dry_run else None,
                skipped_count=skipped_count if not args.dry_run else None,
                failed_repos=failed_repos if not args.dry_run else None,
            )
        except Exception as e:
            logging.warning("Summary generation failed: %s", e)

    # Exit non-zero if any issue creation failed (so the workflow step fails)
    if failed_repos and not args.dry_run:
        logging.error("Issue creation failed for %d repo(s): %s", len(failed_repos), ", ".join(failed_repos))
        return 2
    return 0

def _secure_entrypoint():
    try:
        code = main()
    except KeyboardInterrupt:
        logging.error("Interrupted by user")
        code = 130
    except Exception as e:
        logging.exception("Fatal error: %s", e)
        code = 1
    sys.exit(code)


if __name__ == "__main__":
    _secure_entrypoint()
