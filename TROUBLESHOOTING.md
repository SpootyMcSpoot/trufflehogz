# Troubleshooting & Performance Tuning

Performance optimization and issue resolution guide.

## Performance Tuning

### Scans Too Slow
- Reduce scope: `branch_strategy: main-only`
- Shorter window: `scan_lookback_days: 1` or `3`
- Reduce lookback: `branch_lookback_days: 7` or `14`
- Increase parallelism: `scan_parallel: 16`
- Enable adaptive timeout: `adaptive_timeout: true` (default)
- Skip stale branches: `skip_stale_branches: true` (default)
- Skip large repos: Set `max_repo_size_kb: 500000` in workflow file

### Missing Findings
- Deep scan: `deep_scan: true`
- Expand scope: `branch_strategy: all` or `main-recent`
- Longer window: `scan_lookback_days: 14` or `30`
- Longer lookback: `branch_lookback_days: 60` or `90`
- More results: `scan_results: all`
- Disable skipping: `skip_stale_branches: false`
- Full history: `scan_mode: full`

### Rate Limits
- Reduce concurrency: `scan_parallel: 4`
- Fewer shards: `shard_cap: 5`
- Built-in limiting: 4 calls/sec (automatic)
- Stagger scans: Run different orgs at different times

### Timeouts
- Enable adaptive: `adaptive_timeout: true` (default)
- Increase timeout: `per_repo_timeout: 10m` or `15m`
- Skip large repos: Set `max_repo_size_kb: 100000`
- Reduce scope: `scan_mode: recent` + shorter `scan_lookback_days`

### Deep Scan Best Practices
- Run monthly/quarterly (not daily)
- Higher timeout: `per_repo_timeout: 10m+`
- Lower parallelism: `scan_parallel: 4-8`
- Monitor for timeouts
- Budget 30-90 min for large orgs

## Monitoring

**Key Metrics**:
- Total scan time (workflow summary)
- Timeout count (errors NDJSON)
- Shard completion time (check imbalance)
- API rate limit hits (workflow logs)

**Optimization Indicators**:
- Shards within 5 min → Good balance
- One shard 2x+ longer → Enable size-based sharding
- Many timeouts → Increase timeout or enable adaptive
- Missing findings → Increase lookback or change strategy

## Quick Diagnostics

**Run the self-scan workflow** (**Actions → Self-Scan Test → Run workflow**) to validate end-to-end:
- Confirms TruffleHog + scanner script are working correctly
- Runs all 4 jobs (plan → scan → summarize → workflow-summary)
- Should detect ≥ 20 test secrets from `test/fixtures/`
- If self-scan passes but org-scan fails, the issue is likely auth or org-specific

## Common Issues

**No findings but artifacts exist**
- Check stderr logs: `tmp-logs-<org>/*.stderr.log`
- Verify false positive patterns aren't too broad
- Check `scan_results` includes desired types

**401/403 errors**
- Classic PAT: Verify `repo` + `read:org` scopes
- Fine-grained PAT: Verify Contents (Read) + Members (Read)
- GitHub App: Verify App ID, private key format, installation ID, and Metadata (Read) permission
- Check token expiration
- Verify org access
- See [SETUP.md](SETUP.md#troubleshooting) or [GITHUB_APP_SETUP.md](GITHUB_APP_SETUP.md#troubleshooting)

**Timeouts on specific repos**
- Enable `adaptive_timeout: true`
- Check repo size (huge histories)
- Add to `max_repo_size_kb` filter

**Missing branches**
- Check `branch_strategy` (try `all` or `main-recent`)
- Increase `branch_lookback_days` to 60-90
- Set `skip_stale_branches: false`
- Use `deep_scan: true`

**Missing old commits**
- Set `scan_mode: full`
- Increase `scan_lookback_days` to 14-30
- Use `deep_scan: true`

**Empty NDJSON files**
- Check TruffleHog version compatibility
- Verify repos aren't all archived/empty
- Review `scan_results` setting
- Check time-based filters aren't excluding all commits

**Deep scan too long**
- Increase `per_repo_timeout` to 15m
- Reduce `scan_parallel` to 4-8
- Enable `adaptive_timeout: true`
- Split into multiple smaller runs

---

**See also**:
- [README.md](README.md) - Overview and full configuration reference
- [SETUP.md](SETUP.md) - Authentication setup
- [GITHUB_APP_SETUP.md](GITHUB_APP_SETUP.md) - GitHub App auth with troubleshooting
- [CONFIGURATION.md](CONFIGURATION.md) - Preset examples
- [REMEDIATION.md](REMEDIATION.md) - Secrets remediation
