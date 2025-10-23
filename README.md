# TruffleHog Organization Scans (Verified-only, Private Repos)

This repository contains:
- A GitHub Actions workflow that scans one or more GitHub organizations for leaked secrets using the TruffleHog OSS CLI in a container.
- A Python tool that post-processes TruffleHog output (NDJSON), filters false positives via regexes, writes a detailed job summary, and (when not dry-run) creates per-repo issues only for verified findings.

## Contents

~~~
.github/
  workflows/
    trufflehog_scans.yml          # CI workflow (dispatch, push; supports sharding and caching)
  trufflehog/
    false_positives.txt           # Regex-based suppressions for recurring false positives
trufflehog/
  trufflehog_scanner.py           # Post-processing, summary, optional issue creation
~~~

## What the workflow does

1) **Resolve inputs and matrix**
   - Accepts `org_names` (comma-separated), `shards` (parallel slices per org), and `exclude_generic` (toggle to skip the Generic detector).
   - Builds a matrix of jobs across orgs and shards for parallel execution.

2) **Cache the TruffleHog image**
   - Pulls `ghcr.io/trufflesecurity/trufflehog:<version>`, caches it as a tar file, and loads from cache in subsequent runs.

3) **Enumerate target repos**
   - Lists **private**, non-archived, non-fork repos in each org via the GitHub API.
   - Optional size limit with `MAX_REPO_SIZE_KB` (off by default).
   - Shards the repo list deterministically across jobs.

4) **Scan each repo with TruffleHog (container)**
   - Uses the `github` provider against `https://github.com/<owner>/<repo>`.
   - Flags:
     - `--results=verified` (verified-only to reduce noise)
     - Optional `--exclude-detectors=generic`
     - `--force-skip-binaries`, `--force-skip-archives`
     - Per-repo timeout via `PER_REPO_TIMEOUT` (default 15m)
   - Writes one NDJSON file **per repo**, then merges them into `findings-<org>.ndjson`.
   - Captures per-repo stderr logs for troubleshooting.

5) **Diagnostics and summary**
   - Runs `trufflehog_scanner.py` in diagnostics mode to print quick stats for the per-repo NDJSON and merged NDJSON.
   - Runs the tool in summary mode to:
     - Print a sanitized preview to the job logs.
     - Write a Markdown summary to the job summary (GITHUB_STEP_SUMMARY).
     - Include a **Detailed findings by repository** section with detector, file, line, and a direct link to the commit line.

6) **Issue creation (dry-run by default)**
   - The workflow currently calls the script with `--dry-run`.
   - When you remove `--dry-run`, the script creates **one issue per repo** per day if verified findings exist and an issue does not already exist for the day. Labels are created on demand as needed.

7) **Artifacts**
   - Uploads:
     - `findings-<org>.ndjson` (merged)
     - `tmp-findings-<org>/*.ndjson` (per repo)
     - `tmp-logs-<org>/*.stderr.log` (stderr previews for debugging)

---

## Script capabilities (`trufflehog/trufflehog_scanner.py`)

- **Verified-only** results are used for issue creation by design.
- **False-positive filtering** via `--exclude-patterns-file`. The file contains newline-delimited regexes (comments start with `#`). Matching findings are dropped from logs, summary, and issue creation. See `.github/trufflehog/false_positives.txt`.
- **Sanitized console preview** that shows `org | repo | detector | file:line | verified | link`.
- **Job summary** with:
  - Total verified findings.
  - Findings by repository and by detector.
  - Optional detailed per-repo table with detector, file, line, and link (caps to avoid huge summaries).
- **Per-repo GitHub issues** (non-dry-run only). Titles are date-based so the script does not open duplicates on the same day.
- **Auto labels**:
  - Baseline: `security`, `secrets`, `tool:trufflehog`, `needs-triage`
  - Detector-based: `secret:aws`, `secret:github`, etc.
  - Severity-based: `sec:low|medium|high|critical` (mapped from detector type).
- **Multi-threaded** issue creation with a small API rate limiter and retry logic.

### Important design choices

- **Verified-only**: The workflow emits only verified results (`--results=verified`) to the NDJSON. This reduces noise but may miss strings where TruffleHog disabled verification due to detector overlap. You can opt in to overlap verification with `--allow-verification-overlap` if needed (not recommended by default).
- **No inline secrets**: All printing is sanitized. The script never prints raw secrets. It uses the commit+path+line for context and link-back.
- **Deterministic merges**: Per-repo NDJSON files are concatenated in a stable order to make outputs repeatable.

---

## Configuration

### Inputs (via Run workflow UI)

~~~
on:
  workflow_dispatch:
    inputs:
      org_names:
        description: "Comma-separated list of orgs"
        required: false
        default: "slac-it,slac-chef,slaclab"
        type: string
      shards:
        description: "Parallel shards per org (1 = off)"
        required: false
        default: 1
        type: number
      exclude_generic:
        description: "Exclude the noisy 'generic' detector?"
        required: false
        default: true
        type: boolean
~~~

- Open the Actions tab, pick the workflow, click **Run workflow**, set inputs, and run.

### Environment variables (top of workflow)

- `TRUFFLEHOG_VERSION` and `TRUFFLEHOG_IMAGE`
- `TRUFFLEHOG_CACHE_FILE` (where the image tar is cached)
- `MAX_FINDING_LOG_LINES` (sanitized preview lines)
- `PER_REPO_TIMEOUT` (e.g., `15m`)
- `SCAN_PAR` (# of repos scanned concurrently in a shard)
- `MAX_REPO_SIZE_KB` (0 = off)

### Token permissions

Use a **classic PAT** for now:

- `repo` (to read private repos)
- `read:org`
- If you enable issue creation: ensure the PAT has rights to create issues in target repos.

Store the PAT as `ORG_PAT` (repository or organization secret).

---

## False positives

Maintain `.github/trufflehog/false_positives.txt`.

Example entries:

~~~
# Example Postgres DSN placeholders in docs (wildcard DB name)
postgres:\/\/username:password@hostname:\d+\/[^\/\s]+

# Obvious placeholders
\bexample(_|-)?(user|username|token|password|pass|key)\b
\busername:password@hostname\b
\bchangeme\b
\bplaceholder\b

# Documentation paths (matches against "file=" portion of composed string)
file=.*\/docs\/
file=.*\/examples?\/
file=.*README(\.md|\.rst)?$
~~~

Notes:
- Lines are case-insensitive regexes.
- Lines starting with `#` are comments.
- The script builds a composed string per finding that includes the detector, file, repo, and redacted token. Matches against that string suppress the finding.

---

## Performance

- **Shards**: Splits each org’s repo list across `shards` parallel jobs. Useful for large orgs. Each shard still runs with `SCAN_PAR` repos in parallel.
- **Timeouts**: Some repos with heavy history need more time; the default is `15m` per repo, adjustable via `PER_REPO_TIMEOUT`.
- **Detector scope**: Excluding the `generic` detector can speed up scans and reduce noise.
- **Size cap**: Set `MAX_REPO_SIZE_KB` to skip very large repos entirely (0 disables the cap).
- **Rate limits**: Parallelism increases API pressure. If you hit rate limits, lower `SCAN_PAR` or `shards`.

---

## Scheduling

If you want a weekly run at Monday 08:00 America/Los_Angeles:

~~~
on:
  schedule:
    # GitHub cron uses UTC. 08:00 PT = 15:00 UTC during PDT, 16:00 UTC during PST.
    - cron: "0 15 * * 1"  # Adjust if you want to pin to PST instead of observing DST
~~~

You can keep `workflow_dispatch` for ad-hoc runs and keep `push` while testing. Remove `push` when finished.

---

## Local testing

### Dry-run post-processing

Given an NDJSON file (from CI or a local TruffleHog run):

~~~
python3 trufflehog/trufflehog_scanner.py \
  --ndjson findings-slac-it.ndjson \
  --org slac-it \
  --exclude-patterns-file .github/trufflehog/false_positives.txt \
  --print-sanitized 50 \
  --write-summary \
  --summary-detailed \
  --summary-detailed-per-repo 10 \
  --summary-detailed-max 300 \
  --run-url "https://github.com/<org>/<repo>/actions/runs/<id>" \
  --max-workers 8 \
  --log-level INFO \
  --dry-run
~~~

### Producing NDJSON locally

Example via Docker against a single repo:

~~~
docker run --rm -v "$PWD:/work" -w /work ghcr.io/trufflesecurity/trufflehog:3.90.6 \
  github --repo "https://github.com/<owner>/<repo>" \
         --token "$GH_PAT" \
         --results=verified \
         --json \
         --force-skip-binaries \
         --force-skip-archives \
  > findings-local.ndjson
~~~

Then run the Python tool as shown above.

---

## What the summary looks like

Example of the job summary section:

~~~
## TruffleHog summary for `slac-it (shard 0/1)`

- Total verified finding(s): **3**

### Findings by repository
| Repository | Count |
|---|---:|
| `slac-it/slac-today` | 2 |
| `slac-it/internal-tools` | 1 |

### Findings by detector
| Detector | Count |
|---|---:|
| Mailchimp | 1 |
| SendGrid | 1 |
| GitHub | 1 |

### Detailed findings by repository

#### `slac-it/slac-today`
| Detector | File | Line | Link |
|---|---|---:|---|
| Mailchimp | `app/config.py` | 42 | https://github.com/slac-it/slac-today/blob/abcdef1/app/config.py#L42 |
| SendGrid  | `services/emailer.py` | 110 | https://github.com/slac-it/slac-today/blob/abcdef2/services/emailer.py#L110 |

#### `slac-it/internal-tools`
| Detector | File | Line | Link |
|---|---|---:|---|
| GitHub | `scripts/release.sh` | 88 | https://github.com/slac-it/internal-tools/blob/1234567/scripts/release.sh#L88 |
~~~

---

## Creating issues for real

The workflow’s “Process findings” step currently includes `--dry-run`. To enable real issue creation:
- Remove `--dry-run` from that step.
- Ensure `ORG_PAT` has permissions to create issues on target repos.
- The script avoids duplicate issues by using a date-based title and checking for an existing open issue with the same title.

---

## Troubleshooting

- **No findings but artifacts are non-empty**: Check `tmp-logs-<org>/*.stderr.log` to see if TruffleHog hit auth or provider errors. Also confirm `--results=verified` is appropriate for your case.
- **Verification disabled message**: TruffleHog may disable verification when multiple detectors match the same candidate. This yields `Verified:false`. Consider `--exclude-detectors=generic` or (only if needed) add `--allow-verification-overlap` to the scan step.
- **404 on repo meta**: Ensure the repo name is `owner/name` and that the token has access. Some repos may have issues disabled or be archived.
- **Time-outs**: Increase `PER_REPO_TIMEOUT` or reduce `SCAN_PAR`/`shards`.
- **Empty merged NDJSON**: Verify that per-repo NDJSON files are present and non-empty; inspect the stderr logs for any errors.

---

## Future enhancements

- **ServiceNow integration**: Collect all repo findings into an INC, add created issue links as a note, and back-reference the INC on each repo issue. The script structure has a clear seam to add this without changing the workflow.
- **Aggregate post-job**: Add a final job that downloads all shard artifacts, merges NDJSON across shards and orgs, and publishes a single top-level summary.
- **GitHub App auth**: Replace PAT with a GitHub App for better scoping and potentially higher rate limits.
- **Single-process multi-repo scan**: Use a TruffleHog config file to scan many repos in one container so the verification cache is shared across repos.
- **Field-scoped FP rules**: Extend false positive rules to allow prefixes like `file:<regex>`, `detector:<regex>`, etc., for more surgical suppressions.
- **Path excludes**: Add first-class path-level suppression in post-processing for docs/examples fixtures.
