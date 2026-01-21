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
- Diagnostics for JSON (per-repo and merged)
- False-positive filtering via regex allow-list file (--exclude-patterns-file)
- Detailed per-repo subsection in summary (detector + link to commit/line)
- Optional errors JSON inclusion (HTTP errors etc.) into the summary

Environment
  GH_APP_ID              GitHub App ID (preferred)
  GH_APP_PRIVATE_KEY     GitHub App private key (PEM format)
  GH_APP_INSTALLATION_ID GitHub App installation ID
  GH_PAT                 GitHub Classic PAT with repo and read:org (fallback)

CLI
  --json PATH            TruffleHog JSON path (default: findings.json)
  --run-url URL          Link back to the workflow run
  --max-workers N        Thread count (default: min(8, cpu_count()*2))
  --labels "a,b,c"       Extra labels to union with auto labels
  --dry-run              Do not write to GitHub
  --title-prefix STR     Issue title prefix (default: "[TruffleHog]")
  --log-level LEVEL      Logging level (default: INFO)
  --print-sanitized N    Print up to N sanitized VERIFIED finding lines to stdout
  --write-summary        Write a Markdown summary to GITHUB_STEP_SUMMARY
  --org STR              Optional org name for display in logs
  --errors-json PATH     Optional JSON of errors (e.g., HTTP 401) to include in summary

Diagnostics
  --diag-dir PATH        Directory of per-repo .json to inspect
  --diag-max-files N     Max number of files to print diagnostics for (default: 25)
  --diag-max-lines N     Max sanitized lines to print per file (default: 3)
  --validate             Print stats about --json (total lines, bad lines, unique repos/detectors) and exit (read-only)

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
from urllib.parse import quote as urlquote, urlparse
from urllib.request import Request, urlopen

try:
    import jwt
    JWT_AVAILABLE = True
except ImportError:
    JWT_AVAILABLE = False
    logging.warning("PyJWT not available, GitHub App authentication disabled")

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
        self.interval = 1.0 / max(0.1, calls_per_sec)
        self.lock = threading.Lock()
        self.last = 0.0

    def wait(self) -> None:
        with self.lock:
            now = time.time()
            wait_for = self.last + self.interval - now
            if wait_for > 0:
                time.sleep(wait_for)
            self.last = time.time()

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
    
    raise ValueError("No authentication method available. Set either GH_PAT or GitHub App credentials (GH_APP_ID, GH_APP_PRIVATE_KEY, GH_APP_INSTALLATION_ID)")

# ---------------------------- Logging ----------------------------

def setup_logging(level: str = "INFO") -> None:
    lvl = getattr(logging, level.upper(), logging.INFO)
    fmt = "%(asctime)s %(levelname)s %(threadName)s %(funcName)s: %(message)s"
    logging.basicConfig(level=lvl, format=fmt)

# ---------------------------- Label taxonomy ----------------------------

DEFAULT_LABELS = ["security", "secrets", "tool:trufflehog"]
SEVERITY_ORDER = ["sec:low", "sec:medium", "sec:high", "sec:critical"]

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

def normalize_detector(name: str) -> str:
    n = (name or "").lower()
    for k in DETECTOR_TYPES:
        if k.lower() in n:
            return k
    if "webhook" in n and "slack" in n:
        return "SlackWebhook"
    if "pem" in n or "private key" in n:
        return "Private Key"
    if "ssh" in n:
        return "SSH"
    if "jwt" in n:
        return "JWT"
    return "Generic"

def labels_for_items(items: List[Dict[str, Any]], extra: Optional[List[str]] = None) -> List[str]:
    labels = set(DEFAULT_LABELS)
    highest = "sec:low"
    for it in items:
        det = normalize_detector(str(it.get("detector", "")))
        sev = DETECTOR_SEVERITY.get(det, "sec:medium")
        typ = DETECTOR_TYPES.get(det, "secret:generic")
        labels.add(sev)
        labels.add(typ)
        if SEVERITY_ORDER.index(sev) > SEVERITY_ORDER.index(highest):
            highest = sev
    labels.add("needs-triage")
    if extra:
        labels.update([s.strip() for s in extra if s.strip()])
    return sorted(labels)

# ---------------------------- Helpers to read fields ----------------------------

# Common source metadata paths for TruffleHog findings (order matters: try most specific first)
_SOURCE_PATHS = (
    ["SourceMetadata", "Data", "Git"],
    ["SourceMetadata", "Data", "GitHub"],
    ["SourceMetadata", "Data"],
)

def deepget(obj: Dict[str, Any], path: List[str]) -> Optional[Any]:
    cur: Any = obj
    for k in path:
        if isinstance(cur, dict) and k in cur:
            cur = cur[k]
        else:
            return None
    return cur

def _get_source_field(obj: Dict[str, Any], field: str) -> Optional[Any]:
    """Extract a field from source metadata, checking all common paths."""
    for base in _SOURCE_PATHS:
        v = deepget(obj, base + [field])
        if v is not None:
            return v
    return None

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
    repo_val = _get_source_field(o, "repository")
    if repo_val:
        repo = extract_repo(repo_val)
        if repo:
            return repo
    # Fallback: search JSON for GitHub URL pattern
    m = re.search(r'https://github\.com/([^/]+)/([^/"\s]+)', json.dumps(o))
    if m:
        return sanitize_repo(f"{m.group(1)}/{m.group(2)}")
    return None

def file_path(o: Dict[str, Any]) -> str:
    v = _get_source_field(o, "file")
    return str(v) if v else ""

def line_no(o: Dict[str, Any]) -> Optional[int]:
    v = _get_source_field(o, "line")
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.isdigit():
        return int(v)
    return None

def commit_sha(o: Dict[str, Any]) -> Optional[str]:
    v = _get_source_field(o, "commit")
    return str(v) if v else None

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
        quoted = urlquote(path, safe="/")
        # NOTE: No line anchor to prevent GitHub's auto code preview
        if commit:
            return f"https://github.com/{repo}/blob/{commit}/{quoted}"
        else:
            return f"https://github.com/{repo}/blob/HEAD/{quoted}"
    if repo and commit:
        return f"https://github.com/{repo}/commit/{commit}"
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
    if not regexes:
        return False
    s = compose_match_string(o)
    for rx in regexes:
        if rx.search(s):
            return True
    return False

# ---------------------------- GitHub API ----------------------------

class GHClient:
    def __init__(self, token: str, dry_run: bool = False):
        self.token = token
        self.api_base = API_BASE
        self.dry_run = dry_run

    def _req(self, method: str, path: str, payload: Optional[dict], attempt: int) -> dict:
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
            logging.warning("URLError on %s %s attempt=%d error=%s", method, path, e)
            raise

    def call(self, method: str, path: str, payload: Optional[dict] = None,
             max_retries: int = DEFAULT_MAX_RETRIES, backoff_base: float = DEFAULT_BACKOFF_BASE) -> dict:
        if self.dry_run and method.upper() in ("POST", "PATCH", "PUT", "DELETE"):
            logging.info("DRY RUN: %s %s payload=%s", method, path, payload)
            return {}
        for attempt in range(1, max_retries + 1):
            try:
                GLOBAL_LIMITER.wait()
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
            # Check first 5 pages (150 issues) for hash matches
            for page in [1, 2, 3, 4, 5]:
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
        while True:
            data = self.call("GET", f"/repos/{repo}/labels?per_page=100&page={page}", None)
            if not isinstance(data, list) or not data:
                break
            names.extend([x.get("name", "") for x in data if isinstance(x, dict)])
            if len(data) < 100:
                break
            page += 1
        return [n for n in names if n]

    def create_labels_if_needed(self, repo: str, labels: List[str]) -> None:
        existing = set(self.get_labels(repo))
        missing = [label for label in labels if label not in existing]
        for lbl in missing:
            try:
                self.call("POST", f"/repos/{repo}/labels", {"name": lbl})
                logging.info("Created label %s in %s", lbl, repo)
            except HTTPError as e:
                if e.code not in (409, 422):
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

def iter_json(path: str, verified_only: bool = False, exclude_regexes: Optional[List[re.Pattern]] = None) -> Generator[Dict[str, Any], None, None]:
    """Generator that yields parsed JSON objects, optionally filtered."""
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
            except json.JSONDecodeError:
                continue

def load_findings(json_path: str, exclude_regexes: List[re.Pattern]) -> Dict[str, List[Dict[str, Any]]]:
    """Read JSON and return findings grouped by repo, filtered to Verified==True and not excluded, de-duped."""
    findings_by_repo: Dict[str, List[Dict[str, Any]]] = {}
    seen = set()

    for obj in iter_json(json_path, verified_only=True, exclude_regexes=exclude_regexes):
        repo = sanitize_repo(find_repo(obj) or "")
        if not repo:
            continue

        item = {
            "detector": obj.get("DetectorName") or obj.get("DetectorType") or "Unknown",
            "file": file_path(obj),
            "line": line_no(obj),
            "commit": commit_sha(obj),
        }

        key = make_finding_key(repo, item["detector"], item["file"], item["line"], item["commit"])
        if key not in seen:
            seen.add(key)
            findings_by_repo.setdefault(repo, []).append(item)

    logging.info("Loaded findings for %d repo(s) (verified and not excluded)", len(findings_by_repo))
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

def print_sanitized_preview(json_path: str, org: str, max_lines: int,
                            exclude_regexes: List[re.Pattern]) -> None:
    """Print up to N sanitized VERIFIED findings to stdout for quick inspection."""
    if max_lines <= 0:
        return
    shown = 0
    for obj in iter_json(json_path, verified_only=True, exclude_regexes=exclude_regexes):
        if shown >= max_lines:
            break
        _print_one_sanitized(obj, org)
        shown += 1
    if shown > 0:
        logging.info("[%s] preview printed %d line(s)", org, shown)

def json_stats(path: str, exclude_regexes: List[re.Pattern], org: str) -> Tuple[int, int, int, int]:
    total, bad, repos, dets = 0, 0, set(), set()
    if not os.path.exists(path):
        logging.info("[%s] JSON not found: %s", org, path)
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
            except json.JSONDecodeError:
                bad += 1
    return (total, bad, len(repos), len(dets))

def load_errors_json(path: Optional[str]) -> List[Dict[str, Any]]:
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
            except json.JSONDecodeError:
                continue
    return errs

def write_markdown_summary(json_path: str,
                           org: str,
                           exclude_regexes: List[re.Pattern],
                           include_detailed: bool = False,
                           detailed_per_repo: int = 10,
                           detailed_max_total: int = 300,
                           errors_path: Optional[str] = None) -> None:
    """
    Build a verified-only summary with strict de-duplication:
    uniqueness key = (repo, detector, file, line, commit).
    Counts and detailed rows reflect unique findings only.
    """
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    unique_seen = set()
    per_repo = collections.Counter()
    per_det = collections.Counter()
    per_repo_rows: Dict[str, List[Tuple[str, str, Optional[int], str]]] = {}
    total = 0

    for o in iter_json(json_path, verified_only=True, exclude_regexes=exclude_regexes):
        det = o.get("DetectorName") or o.get("DetectorType") or "Unknown"
        repo = sanitize_repo(find_repo(o) or "")
        if not repo:
            continue

        fpath = file_path(o)
        ln = line_no(o)
        sha = commit_sha(o)
        link = line_link(repo, sha, fpath, ln)

        key = make_finding_key(repo, det, fpath, ln, sha)
        if key in unique_seen:
            continue
        unique_seen.add(key)

        total += 1
        per_repo[repo] += 1
        per_det[det] += 1

        if include_detailed:
            per_repo_rows.setdefault(repo, []).append((det, fpath, ln, link))

    content_lines: List[str] = []
    content_lines.append(f"## TruffleHog summary for `{org}`\n")
    content_lines.append(f"- Total verified unique finding(s): **{total}**\n")

    if per_repo:
        content_lines.append("### Findings by repository (unique, verified)\n")
        content_lines.append("| Repository | Unique verified |\n|---|---:|\n")
        for r, c in per_repo.most_common():
            content_lines.append(f"| `{r}` | {c} |\n")
        content_lines.append("\n")

    if per_det:
        content_lines.append("### Findings by detector (unique, verified)\n")
        content_lines.append("| Detector | Unique verified |\n|---|---:|\n")
        for d, c in per_det.most_common():
            content_lines.append(f"| {d} | {c} |\n")
        content_lines.append("\n")

    if include_detailed and per_repo_rows:
        content_lines.append("### Detailed verified findings by repository (unique)\n")
        total_rows = 0
        for repo, _cnt in per_repo.most_common():
            rows = list(dict.fromkeys(per_repo_rows.get(repo, [])))  # De-dupe while preserving order
            if not rows or total_rows >= max(1, detailed_max_total):
                continue

            content_lines.append(f"#### `{repo}`\n")
            content_lines.append("| Detector | File | Line | Link |\n|---|---|---:|---|\n")
            for idx, (det, fpath, ln, link) in enumerate(rows):
                if idx >= max(1, detailed_per_repo) or total_rows >= max(1, detailed_max_total):
                    break
                ln_str = str(ln) if ln is not None else ""
                content_lines.append(f"| {det} | `{fpath}` | {ln_str} | {link or ''} |\n")
                total_rows += 1
            content_lines.append("\n")

        if total_rows >= max(1, detailed_max_total):
            remaining = sum(len(v) for v in per_repo_rows.values()) - total_rows
            if remaining > 0:
                content_lines.append(f"_... {remaining} more finding(s) omitted to keep the summary concise._\n\n")

    errs = load_errors_json(errors_path)
    if errs:
        content_lines.append("### API/Scan errors observed\n")
        content_lines.append("| Repo | Stage | Status | Message |\n|---|---|---:|---|\n")
        for e in errs:
            content_lines.append(f"| `{e.get('repo','')}` | {e.get('stage','')} | {e.get('status','')} | {str(e.get('message','')).replace('|','&#124;')} |\n")
        content_lines.append("\n")

    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as out:
            out.writelines(content_lines)
        print(f"[{org}] summary written (unique verified total={total})")
    else:
        print("".join(content_lines))

# ---------------------------- Issue creation ----------------------------

def compute_findings_hash(items: List[Dict[str, Any]]) -> str:
    """
    Compute a deterministic hash from findings to uniquely identify this set of secrets.

    Uses file paths, line numbers, and commits to create a stable identifier.
    Same findings = same hash, allowing duplicate detection even across days.
    """
    import hashlib

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
    if not meta:
        return False
    if meta.get("archived"):
        logging.info("Repo archived, skipping")
        return False
    if not meta.get("has_issues", True):
        logging.info("Issues disabled, skipping")
        return False
    return True

def build_issue_body(repo: str, items: List[Dict[str, Any]], run_url: str, scanner_repo: str = "") -> str:
    count_text = "1 verified secret" if len(items) == 1 else f"{len(items)} verified secrets"
    header = [
        "## Secret Detection Alert",
        "",
        f"**Status**: {count_text} found in this repository",
        f"**Scan**: [View workflow run]({run_url})" if run_url else "",
        "",
        "> **SECURITY NOTICE**: Do not paste secret values in this issue. All secrets below should be considered compromised.",
        "",
        "### Findings",
        "",
        "| Detector | File | Line | Link |",
        "|---|---|---:|---|",
    ]
    rows = []
    for it in items:
        path = it.get("file", "") or ""
        ln = it.get("line")
        sha = it.get("commit")
        link_url = line_link(repo, sha, path, ln)
        ln_str = str(ln) if ln is not None else ""

        # Format link as markdown to make it clickable
        link_display = f"[View]({link_url})" if link_url else ""

        rows.append(f"| {it.get('detector','')} | `{path}` | {ln_str} | {link_display} |")

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
    ]

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
                 scanner_repo: str = "") -> Tuple[str, bool]:
    logging.info("Processing repo %s with %d finding(s)", repo, len(items))
    meta = gh.repo_meta(repo)
    if not repo_is_actionable(meta):
        return (repo, False)

    # Compute unique hash for these findings to enable duplicate detection
    findings_hash = compute_findings_hash(items)
    today = datetime.date.today().isoformat()
    title = f"{title_prefix} Secrets scan report - {today} ({findings_hash})"

    # Check for exact match (same findings, same day)
    if gh.search_issue_by_title(repo, title):
        logging.info("Issue already exists for %s with same findings (hash=%s)", repo, findings_hash)
        return (repo, False)

    # Check for issues with same findings hash within last 90 days (open or closed)
    # This prevents re-notifying about secrets that were already reported
    title_pattern = f"{title_prefix} Secrets scan report"
    hash_pattern = f"({findings_hash})"
    if gh.search_recent_issues_with_hash(repo, title_pattern, hash_pattern, days=90):
        logging.info("Issue already exists in %s with same findings (hash=%s), skipping", repo, findings_hash)
        return (repo, False)

    auto = labels_for_items(items, extra_labels)
    try:
        gh.create_labels_if_needed(repo, auto)
    except HTTPError as e:
        logging.warning("Skipping label creation in %s due to HTTP %d; continuing to issue body.", repo, e.code)

    body = build_issue_body(repo, items, run_url, scanner_repo)
    try:
        created = gh.create_issue(repo, title, body, auto) or {}
        issue_number = created.get("number")
        logging.info("Created issue in %s number=%s", repo, issue_number)
        return (repo, True)
    except HTTPError as e:
        logging.warning("Failed to create issue in %s: HTTP %d", repo, e.code)
        return (repo, False)

# ---------------------------- Diagnostics helpers ----------------------------

def diagnose_findings_dir(diag_dir: str, org: str, max_files: int, max_lines: int,
                          exclude_regexes: Optional[List[re.Pattern]] = None) -> None:
    """Print quick diagnostics for per-repo JSON files."""
    if not diag_dir or not os.path.isdir(diag_dir):
        logging.info("[%s] diag dir not found: %s", org, diag_dir)
        return

    printed = 0
    for root, _dirs, files in os.walk(diag_dir):
        for fn in sorted(files):
            if not fn.endswith(".json") or printed >= max_files:
                continue
            path = os.path.join(root, fn)
            print(f"[{org}] diag file: {path} bytes={os.path.getsize(path)}")

            c = 0
            for obj in iter_json(path):
                if c >= max_lines:
                    break
                _print_one_sanitized(obj, org)
                c += 1
            printed += 1

# ---------------------------- CLI and main ----------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--json", default="findings.json")
    p.add_argument("--run-url", default="")
    p.add_argument("--scanner-repo", default="", help="GitHub repository with remediation docs (org/repo)")
    p.add_argument("--max-workers", type=int, default=0)
    p.add_argument("--labels", default="")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--title-prefix", default="[TruffleHog]")
    p.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "INFO"))
    p.add_argument("--print-sanitized", type=int, default=int(os.environ.get("MAX_FINDING_LOG_LINES", "0")))
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

    # Errors JSON (optional)
    p.add_argument("--errors-json", default=None)
    return p.parse_args()

def main() -> int:
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

    # Optional preview for merged JSON (filtered by exclude patterns + Verified)
    if os.path.exists(args.json):
        try:
            print_sanitized_preview(args.json, args.org, args.print_sanitized, exclude_regexes)
        except Exception as e:
            logging.warning("Sanitized preview failed: %s", e)

    # Read-only validation stats
    if args.validate:
        try:
            total, bad, repo_cnt, det_cnt = json_stats(args.json, exclude_regexes, args.org)
            logging.info("[%s] stats: lines=%d bad=%d unique_repos=%d unique_detectors=%d",
                         args.org, total, bad, repo_cnt, det_cnt)
        except Exception as e:
            logging.warning("validate stats failed: %s", e)
        return 0

    # If this invocation is clearly diagnostics-only (no summary requested and no JSON), exit quietly.
    if args.diag_dir and not args.write_summary and not os.path.exists(args.json):
        logging.info("[%s] diagnostics-only run; no merged JSON to process", args.org)
        return 0

    # Optional summary (still read-only)
    if os.path.exists(args.json) and args.write_summary:
        try:
            write_markdown_summary(
                args.json,
                args.org,
                exclude_regexes,
                include_detailed=args.summary_detailed,
                detailed_per_repo=max(1, args.summary_detailed_per_repo),
                detailed_max_total=max(1, args.summary_detailed_max),
                errors_path=args.errors_json
            )
        except Exception as e:
            logging.warning("Summary generation failed: %s", e)

    try:
        token = get_auth_token()
    except ValueError as e:
        logging.info("Skipping issue creation: %s", e)
        return 0 if os.path.exists(args.json) else 0

    findings = load_findings(args.json, exclude_regexes)
    if not findings:
        logging.info("No verified findings detected after filtering; no issues created")
        return 0

    gh = GHClient(token=token, dry_run=args.dry_run)
    extra_labels = [s.strip() for s in args.labels.split(",") if s.strip()] if args.labels else None
    max_workers = args.max_workers or max(1, min(8, (os.cpu_count() or 2) * 2))

    created_count = 0
    with cf.ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="issue") as pool:
        futures = []
        for repo, items in findings.items():
            futures.append(pool.submit(
                process_repo, repo, items, gh, args.title_prefix, args.run_url, extra_labels, args.dry_run, args.scanner_repo
            ))
        for fut in cf.as_completed(futures):
            try:
                repo, created = fut.result()
                if created:
                    created_count += 1
            except Exception as e:
                logging.error("Worker failed: %s", e)

    logging.info("Issues created: %d", created_count)
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

_secure_entrypoint()
