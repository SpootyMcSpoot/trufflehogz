# Performance Improvements for Large Organizations

## 🎯 Current Performance Characteristics

### Bottlenecks Identified

1. **Sequential API calls** for repo enumeration (paginated)
2. **Docker image load** on each shard (even with cache)
3. **All-branches scanning** (`ALL_HEADS=true`) scans every branch
4. **Full history scanning** (`SCAN_HISTORY=true`) scans entire git history
5. **Per-repo timeouts** can waste time on stalled repos
6. **Serial artifact download** in summarize step
7. **No incremental scanning** - always scans everything

### Current Runtime Estimates

| Org Size | Repos | Shards | Estimated Time | Limiting Factor |
|----------|-------|--------|----------------|-----------------|
| Small | 25 | 1 | 3-5 min | Startup overhead |
| Medium | 150 | 3 | 8-12 min | Repo scanning |
| Large | 500 | 10 | 15-25 min | Parallel limit (256 jobs) |
| Very Large | 1500+ | 20+ | 30-60+ min | Job concurrency, timeouts |

## 🚀 Recommended Improvements (Priority Order)

### 1. **Incremental Scanning** (HIGH IMPACT)
Scan only changed files since last successful run.

**Implementation**:
```yaml
inputs:
  scan_mode:
    description: "Scan mode"
    type: choice
    options:
      - "full"
      - "incremental"  # Only scan commits since last run
      - "recent"       # Only scan last 90 days
    default: "incremental"

  lookback_days:
    description: "Days to look back (incremental/recent mode)"
    type: string
    default: "7"
```

**Changes needed**:
```bash
# Store last scan timestamp in artifact or repo variable
LAST_SCAN=$(date -d "7 days ago" +%s)

# TruffleHog supports --since-commit
th_git "${REPO_URL}" --since-commit "${SINCE_COMMIT}" "${EXTRA_FLAGS[@]}"
```

**Expected Impact**:
- Initial run: Same time
- Subsequent runs: **70-90% faster** (only scans new commits)

---

### 2. **Smart Branch Filtering** (HIGH IMPACT)
Only scan main/master + recent branches instead of all branches.

**Implementation**:
```yaml
inputs:
  branch_strategy:
    description: "Branch scanning strategy"
    type: choice
    options:
      - "all"           # Current behavior
      - "main-only"     # Only default branch
      - "main-recent"   # Default + branches updated in last 30 days
      - "protected"     # Only protected branches
    default: "main-recent"
```

**Changes needed**:
```bash
list_branches_api() {
  # ... existing code ...

  if [ "${BRANCH_STRATEGY}" = "main-only" ]; then
    # Get default branch only
    jq -r '.default_branch' < "${repo_meta}" > "branches-${full//\//__}.txt"
  elif [ "${BRANCH_STRATEGY}" = "main-recent" ]; then
    # Filter branches by commit date (last 30 days)
    jq -r '.[] | select(.commit.commit.author.date > "'$CUTOFF_DATE'") | .name'
  fi
}
```

**Expected Impact**:
- Large repos with many branches: **40-60% faster**
- Repos with 100+ branches: **50-80% faster**

---

### 3. **Parallel Artifact Upload/Download** (MEDIUM IMPACT)
Use `merge-multiple: false` and parallel downloads.

**Implementation**:
```yaml
- name: Download shard artifacts (parallel)
  uses: actions/download-artifact@v4
  with:
    pattern: trufflehog-findings-${{ env.ORG }}-shard-*-of-*
    path: merge/${{ env.ORG }}
    merge-multiple: false  # Keep shards separate for parallel processing
```

**Expected Impact**: **10-20% faster** for orgs with 10+ shards

---

### 4. **Repository Size-Based Sharding** (MEDIUM IMPACT)
Distribute repos by estimated scan time, not just count.

**Implementation**:
```bash
# In plan job, collect repo sizes
repos_with_size=$(jq -r '.[] | "\(.full_name):\(.size)"' < "${body}")

# Sort by size and distribute evenly
# Large repos go to different shards to balance load
sort_and_distribute_by_size() {
  # Assign repos round-robin sorted by size (largest first)
  # This ensures each shard has similar total workload
}
```

**Expected Impact**: **15-30% faster** (eliminates shard imbalance)

---

### 5. **Docker Image Optimization** (MEDIUM IMPACT)

**Option A**: Pre-pull to GitHub runner cache
```yaml
services:
  trufflehog:
    image: ghcr.io/trufflesecurity/trufflehog:3.90.6
    # Keeps image warm across jobs
```

**Option B**: Use GitHub Container Registry mirror
```yaml
env:
  TRUFFLEHOG_IMAGE: "ghcr.io/<your-org>/trufflehog:3.90.6"
  # Mirror to your org for faster pulls
```

**Expected Impact**: **1-2 min saved per shard** (startup time)

---

### 6. **Early Termination on Timeout** (LOW IMPACT)
Kill stalled scans immediately instead of waiting for timeout.

**Implementation**:
```bash
scan_one() {
  timeout --kill-after=10s "${PER_REPO_TIMEOUT}" \
    th_git "${REPO_URL}" "${EXTRA_FLAGS[@]}" > "$out" 2> "$err" || {
      echo "[${ORG}] ${full} TIMEOUT after ${PER_REPO_TIMEOUT}" | tee -a "errors-${ORG}.ndjson"
      return 1
    }
}
```

**Expected Impact**: **5-10% faster** (less wasted time)

---

### 7. **Differential Scanning with Commit Cache** (HIGH IMPACT, HIGH EFFORT)
Cache scan results per commit SHA.

**Implementation**:
```yaml
- name: Restore commit scan cache
  uses: actions/cache@v4
  with:
    path: .trufflehog_commit_cache
    key: trufflehog-commits-${{ hashFiles('repos-*.txt') }}
    restore-keys: trufflehog-commits-

- name: Scan with commit-level caching
  run: |
    # Check if commit was scanned before
    if [ -f ".trufflehog_commit_cache/${COMMIT_SHA}.done" ]; then
      echo "Skip ${COMMIT_SHA} (cached clean)"
    else
      scan_commit "${COMMIT_SHA}"
    fi
```

**Expected Impact**:
- First run: Same
- Subsequent runs: **80-95% faster** (only new commits)

---

### 8. **Adaptive Timeout Based on Repo Size** (LOW IMPACT)

**Implementation**:
```bash
# Calculate timeout based on repo size
calc_timeout() {
  local size_kb=$1
  if [ "${size_kb}" -lt 1000 ]; then
    echo "2m"
  elif [ "${size_kb}" -lt 10000 ]; then
    echo "5m"
  elif [ "${size_kb}" -lt 100000 ]; then
    echo "10m"
  else
    echo "15m"
  fi
}
```

**Expected Impact**: **5-10% faster** (less waiting on small repos)

---

### 9. **Skip Binary-Heavy Repos** (LOW IMPACT)

**Implementation**:
```yaml
inputs:
  skip_languages:
    description: "Skip repos with these primary languages (comma-separated)"
    default: ""  # e.g., "Jupyter Notebook,HTML,CSS"
```

**Expected Impact**: **10-20% faster** if filtering out non-code repos

---

### 10. **Regional Runner Selection** (LOW IMPACT)
Use self-hosted runners in same region as GitHub.

**Implementation**:
```yaml
runs-on: self-hosted-us-east  # Closer to github.com
```

**Expected Impact**: **5-10% faster** (network latency)

---

## 🎯 Quick Wins (Implement First)

### Phase 1: Low-Hanging Fruit (1-2 hours)
1. **Smart branch filtering** (main-recent mode)
2. **Adaptive timeouts**
3. **Early termination on timeout**

**Expected Total Impact**: **30-50% faster** for large orgs

### Phase 2: Medium Effort (4-6 hours)
4. **Size-based sharding**
5. **Parallel artifact processing**
6. **Repository size filtering**

**Expected Total Impact**: **50-70% faster** combined with Phase 1

### Phase 3: High Effort (1-2 days)
7. **Incremental scanning with timestamps**
8. **Commit-level caching**

**Expected Total Impact**: **80-95% faster** for recurring scans

---

## 📊 Expected Performance After All Improvements

| Org Size | Repos | Current Time | After Phase 1 | After Phase 2 | After Phase 3 |
|----------|-------|--------------|---------------|---------------|---------------|
| Small | 25 | 3-5 min | 2-3 min | 2-3 min | 1-2 min |
| Medium | 150 | 8-12 min | 5-7 min | 4-6 min | 2-3 min |
| Large | 500 | 15-25 min | 8-15 min | 6-10 min | 2-5 min |
| Very Large | 1500 | 30-60 min | 15-35 min | 10-25 min | 3-8 min |

---

## 🛠️ Implementation Priority

### Immediate (Add to this PR or next one)
```yaml
# Add these inputs to workflow_dispatch
branch_strategy: "main-recent"
scan_mode: "recent"
lookback_days: "7"
adaptive_timeout: true
skip_stale_branches: true
```

### Short-term (Next sprint)
- Size-based shard distribution
- Commit timestamp tracking
- Parallel artifact processing

### Long-term (Future enhancement)
- Full incremental scanning with commit cache
- Self-hosted runners with faster networking
- Custom TruffleHog image with optimizations

---

## 🎨 Example: Optimized Configuration for Large Org

```yaml
# .github/workflows/trufflehog-org-scan.yml
# Optimized for 1000+ repo org
inputs:
  repos_per_shard: 75          # More repos per shard (fewer jobs)
  shard_cap: 15                # Allow more shards
  scan_parallel: 12            # More concurrent scans
  per_repo_timeout: "10m"      # Longer timeout for large repos
  branch_strategy: "main-recent"  # Only recent branches
  scan_mode: "recent"          # Only last 7 days
  lookback_days: "7"
  max_repo_size_kb: "500000"   # Skip repos > 500MB
```

**Expected runtime**:
- First run: 15-20 min (vs 45-60 min currently)
- Daily runs: 3-5 min (vs 45-60 min)

---

## 📈 Monitoring Recommendations

Add these metrics to workflow summary:

```bash
echo "## Performance Metrics"
echo "| Metric | Value |"
echo "|--------|-------|"
echo "| Total scan time | ${ELAPSED_MIN} min |"
echo "| Repos scanned | ${SCANNED_COUNT} |"
echo "| Repos skipped (cached) | ${SKIPPED_COUNT} |"
echo "| Average time per repo | ${AVG_TIME_SEC} sec |"
echo "| Slowest repo | ${SLOWEST_REPO} (${MAX_TIME} sec) |"
echo "| Timeout count | ${TIMEOUT_COUNT} |"
```

---

## 🔗 References

- TruffleHog `--since-commit` flag: https://github.com/trufflesecurity/trufflehog#git-options
- GitHub Actions job concurrency limits: https://docs.github.com/en/actions/learn-github-actions/usage-limits
- Artifact upload/download optimization: https://github.com/actions/upload-artifact#performance

---

## ✅ Action Items

**Ready to implement**:
- [ ] Add `branch_strategy` input with "main-recent" option
- [ ] Add `scan_mode` input with "recent" option
- [ ] Implement adaptive timeouts based on repo size
- [ ] Add performance metrics to workflow summary
- [ ] Document optimal configurations for different org sizes

**Future enhancements**:
- [ ] Size-based shard distribution algorithm
- [ ] Commit-level caching system
- [ ] Incremental scanning with state persistence
- [ ] Performance benchmarking across different strategies
