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
  GH_PAT                 GitHub Classic PAT with repo and read:org

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
import json
import logging
import os
import random
import re
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus, quote as urlquote, urlparse
from urllib.request import Request, urlopen

API_BASE = "https://api.github.com"

# ---------------------------- Rate limiting ----------------------------

class RateLimiter:
    def __init__(self, calls_per_sec: float = 4.0):
        self.interval = 1.0 / max(0.1, calls_per_sec)
        self.lock = threading.Lock()
        self.last = 0.0

    def wait(self):
        with self.lock:
            now = time.time()
            wait_for = self.last + self.interval - now
            if wait_for > 0:
                time.sleep(wait_for)
            self.last = time.time()

GLOBAL_LIMITER = RateLimiter(calls_per_sec=4.0)

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
        deepget(o, ["SourceMetadata", "Data", "GitHub", "repository"]),
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
        ["SourceMetadata", "Data", "GitHub", "file"],
        ["SourceMetadata", "Data", "file"],
    ):
        v = deepget(o, path)
        if v:
            return str(v)
    return ""

def line_no(o: Dict[str, Any]) -> Optional[int]:
    for path in (
        ["SourceMetadata", "Data", "Git", "line"],
        ["SourceMetadata", "Data", "GitHub", "line"],
        ["SourceMetadata", "Data", "line"],
    ):
        v = deepget(o, path)
        if isinstance(v, int) or (isinstance(v, str) and v.isdigit()):
            return int(v)
    return None

def commit_sha(o: Dict[str, Any]) -> Optional[str]:
    for path in (
        ["SourceMetadata", "Data", "Git", "commit"],
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
    if repo and path:
        quoted = urlquote(path, safe="/")
        anchor = f"#L{line}" if line else ""
        if commit:
            return f"https://github.com/{repo}/blob/{commit}/{quoted}{anchor}"
        else:
            return f"https://github.com/{repo}/blob/HEAD/{quoted}{anchor}"
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
            with urlopen(req, timeout=30) as resp:
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
             max_retries: int = 5, backoff_base: float = 1.5) -> dict:
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
                sleep_s = min(16.0, backoff_base ** attempt) + random.uniform(0, 0.5)
                time.sleep(sleep_s)
            except URLError:
                if attempt >= max_retries:
                    logging.error("Max retries reached for %s %s", method, path)
                    raise
                sleep_s = min(16.0, backoff_base ** attempt) + random.uniform(0, 0.5)
                time.sleep(sleep_s)
        return {}

    def repo_meta(self, repo: str) -> Optional[dict]:
        try:
            return self.call("GET", f"/repos/{repo}", None)
        except HTTPError as e:
            logging.warning("Failed to get repo meta for %s: HTTP %d", repo, e.code)
            return None

    def search_issue_by_title(self, repo: str, title: str) -> bool:
        """Search for an open issue by exact title match."""
        q = f'repo:{repo} in:title "{title}" state:open'
        path = f"/search/issues?q={quote_plus(q)}&per_page=1"
        try:
            data = self.call("GET", path, None)
            return bool(data.get("total_count", 0))
        except HTTPError as e:
            logging.warning("Failed to search issues in %s: HTTP %d (may create duplicate)", repo, e.code)
            return False

    def search_recent_issues(self, repo: str, title_pattern: str, days: int = 7) -> bool:
        """Search for recent issues matching a title pattern (partial match)."""
        import datetime
        cutoff_date = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
        q = f'repo:{repo} in:title "{title_pattern}" state:open created:>={cutoff_date}'
        path = f"/search/issues?q={quote_plus(q)}&per_page=5"
        try:
            data = self.call("GET", path, None)
            count = data.get("total_count", 0)
            if count > 0:
                logging.info("Found %d recent open issue(s) in %s matching '%s'", count, repo, title_pattern)
            return count > 0
        except HTTPError as e:
            logging.warning("Failed to search recent issues in %s: HTTP %d", repo, e.code)
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
        missing = [l for l in labels if l not in existing]
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
                     line: Optional[int], commit: Optional[str]) -> Tuple[str, str, str, Optional[int], Optional[str]]:
    """Stable key to identify a unique finding instance."""
    return (sanitize_repo(repo or ""), str(detector or ""), str(path or ""), line, str(commit) if commit else None)

def iter_ndjson(path: str, verified_only: bool = False, exclude_regexes: Optional[List[re.Pattern]] = None) -> Any:
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

def load_findings(ndjson_path: str, exclude_regexes: List[re.Pattern]) -> Dict[str, List[Dict[str, Any]]]:
    """Read NDJSON and return findings grouped by repo, filtered to Verified==True and not excluded, de-duped."""
    findings_by_repo: Dict[str, List[Dict[str, Any]]] = {}
    seen = set()

    for obj in iter_ndjson(ndjson_path, verified_only=True, exclude_regexes=exclude_regexes):
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

    for o in iter_ndjson(ndjson_path, verified_only=True, exclude_regexes=exclude_regexes):
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
        print(f"[{org}] summary written (unique verified total={total})")
    else:
        print("".join(content_lines))

# ---------------------------- Issue creation ----------------------------

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

def build_issue_body(repo: str, items: List[Dict[str, Any]], run_url: str) -> str:
    header = [
        f"Automated TruffleHog (OSS) scan found {len(items)} verified secret(s) in this repository.",
        "",
        f"Scan run: {run_url}" if run_url else "",
        "",
        "> Do not paste secrets in this issue. Rotate or revoke credentials and clean history as appropriate.",
        "",
        "| Detector | File | Line | Link |",
        "|---|---|---:|---|",
    ]
    rows = []
    for it in items:
        path = it.get("file", "") or ""
        ln = it.get("line")
        sha = it.get("commit")
        link = line_link(repo, sha, path, ln)
        ln_str = str(ln) if ln is not None else ""
        rows.append(f"| {it.get('detector','')} | `{path}` | {ln_str} | {link} |")
    guidance = [
        "",
        "Next steps",
        "- Rotate or revoke affected credentials.",
        "- Remove the secret and rewrite history if needed (for example, BFG or git filter-repo).",
        "- Add pre-commit and CI secret scanning gates to prevent reintroduction.",
    ]
    return "\n".join([s for s in header if s != ""] + rows + guidance)

def process_repo(repo: str,
                 items: List[Dict[str, Any]],
                 gh: GHClient,
                 title_prefix: str,
                 run_url: str,
                 extra_labels: Optional[List[str]],
                 dry_run: bool) -> Tuple[str, bool]:
    logging.info("Processing repo %s with %d finding(s)", repo, len(items))
    meta = gh.repo_meta(repo)
    if not repo_is_actionable(meta):
        return (repo, False)

    today = datetime.date.today().isoformat()
    title = f"{title_prefix} Secrets scan report - {today}"

    # Check for exact match (same day issue)
    if gh.search_issue_by_title(repo, title):
        logging.info("Open issue already exists today for %s", repo)
        return (repo, False)

    # Check for recent issues (within 7 days) to avoid spam
    title_pattern = f"{title_prefix} Secrets scan report"
    if gh.search_recent_issues(repo, title_pattern, days=7):
        logging.info("Recent open issue exists in %s, skipping to avoid duplicates", repo)
        return (repo, False)

    auto = labels_for_items(items, extra_labels)
    try:
        gh.create_labels_if_needed(repo, auto)
    except HTTPError as e:
        logging.warning("Skipping label creation in %s due to HTTP %d; continuing to issue body.", repo, e.code)

    body = build_issue_body(repo, items, run_url)
    try:
        created = gh.create_issue(repo, title, body, auto) or {}
        issue_number = created.get("number")
        logging.info("Created issue in %s number=%s", repo, issue_number)
        return (repo, True)
    except HTTPError as e:
        logging.warning("Failed to create issue in %s: HTTP %d", repo, e.code)
        return (repo, False)

# ---------------------------- Diagnostics helpers ----------------------------

def diagnose_findings_dir(diag_dir: str, org: str, max_files: int, max_lines: int) -> None:
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

    # Errors NDJSON (optional)
    p.add_argument("--errors-ndjson", default=None)
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

    # Optional summary (still read-only)
    if os.path.exists(args.ndjson) and args.write_summary:
        try:
            write_markdown_summary(
                args.ndjson,
                args.org,
                exclude_regexes,
                include_detailed=args.summary_detailed,
                detailed_per_repo=max(1, args.summary_detailed_per_repo),
                detailed_max_total=max(1, args.summary_detailed_max),
                errors_path=args.errors_ndjson
            )
        except Exception as e:
            logging.warning("Summary generation failed: %s", e)

    token = os.environ.get("GH_PAT")
    if not token:
        logging.info("Skipping issue creation: GH_PAT is not set")
        return 0 if os.path.exists(args.ndjson) else 0

    findings = load_findings(args.ndjson, exclude_regexes)
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
                process_repo, repo, items, gh, args.title_prefix, args.run_url, extra_labels, args.dry_run
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
