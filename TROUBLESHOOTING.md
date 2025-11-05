# Troubleshooting and Performance Tuning

Complete guide for troubleshooting issues and optimizing performance.

## Table of Contents
- [Performance Tuning](#performance-tuning)
- [Monitoring](#monitoring)
- [Common Issues](#common-issues)

## Performance Tuning

### If Scans Are Too Slow
1. **Reduce branch scope**: Use `branch_strategy: main-only`
2. **Shorten time window**: Set `scan_lookback_days: 1` or `3`
3. **Reduce branch lookback**: Set `branch_lookback_days: 7` or `14`
4. **Increase parallelism**: Set `scan_parallel: 16`
5. **Enable adaptive timeout**: Set `adaptive_timeout: true` (enabled by default)
6. **Skip stale branches**: Ensure `skip_stale_branches: true` (enabled by default)
7. **Skip large repos**: Edit workflow file to set `max_repo_size_kb: 500000`

### If Missing Findings
1. **Use deep scan mode**: Set `deep_scan: true` for comprehensive scanning
2. **Expand branch scope**: Use `branch_strategy: all` or `main-recent`
3. **Expand time window**: Set `scan_lookback_days: 14` or `30`
4. **Expand branch lookback**: Set `branch_lookback_days: 60` or `90`
5. **Include more results**: Set `scan_results: all`
6. **Disable branch skipping**: Set `skip_stale_branches: false`
7. **Full history scan**: Set `scan_mode: full`

### If Hitting Rate Limits
1. **Reduce concurrent scans**: Set `scan_parallel: 4`
2. **Reduce shard count**: Set `shard_cap: 5`
3. **Add delays**: The workflow has built-in rate limiting (4 calls/sec)
4. **Stagger scan times**: Run scans at different times for different orgs

### Timeout Issues
1. **Enable adaptive timeout**: Set `adaptive_timeout: true` (enabled by default)
2. **Increase base timeout**: Set `per_repo_timeout: 10m` or `15m`
3. **Skip large repos**: Edit workflow file to set `max_repo_size_kb: 100000`
4. **Reduce scan scope**: Use `scan_mode: recent` with shorter `scan_lookback_days`

### Deep Scan Best Practices
When using `deep_scan: true`:
1. **Run less frequently**: Monthly or quarterly, not daily
2. **Increase timeout**: Set `per_repo_timeout: 10m` or `15m`
3. **Reduce parallelism**: Set `scan_parallel: 4` or `8` to avoid rate limits
4. **Monitor for timeouts**: Check logs and adjust timeout as needed
5. **Expect longer runtime**: Budget 30-90 minutes for large organizations

## Monitoring

### Key Metrics to Watch
- **Total scan time**: From workflow summary
- **Timeout count**: In errors NDJSON
- **Shard completion time**: Check for imbalance
- **API rate limit hits**: In workflow logs

### Optimization Indicators
- All shards complete within 5 min of each other → Good load balancing
- One shard takes 2x+ longer → Enable `enable_size_based_sharding`
- Many timeouts → Increase `per_repo_timeout` or enable `adaptive_timeout`
- Missing findings → Increase `scan_lookback_days` or change `branch_strategy`

## Common Issues

### No findings in summary but artifacts exist
- Check stderr logs: `tmp-logs-<org>/*.stderr.log`
- Verify false positive patterns aren't too broad
- Ensure `scan_results` includes desired result types

### 401/403 errors
- Classic PAT: Verify `repo` and `read:org` scopes are selected
- Fine-grained PAT: Verify "Contents: Read" and "Members: Read" permissions
- Check PAT hasn't expired
- Confirm user has access to organizations
- See detailed troubleshooting in [Setup Guide - Troubleshooting Token Permissions](SETUP.md#troubleshooting-token-permissions)

### Timeouts on specific repos
- Enable `adaptive_timeout: true`
- Check repo size (some repos have huge histories)
- Consider adding to `max_repo_size_kb` filter

### Missing branches
- Check `branch_strategy` setting (try `all` or `main-recent`)
- Review `branch_lookback_days` value (increase to 60 or 90)
- Set `skip_stale_branches: false` to scan all branches
- Consider using `deep_scan: true` for comprehensive coverage

### Missing old commits
- Set `scan_mode: full` instead of `recent`
- Increase `scan_lookback_days` to 14, 30, or more
- Use `deep_scan: true` for complete history scan

### Empty NDJSON files
- Check TruffleHog version compatibility
- Verify repos aren't all archived/empty
- Review `scan_results` setting
- Confirm time-based filters aren't excluding all commits

### Deep scan taking too long
- Increase `per_repo_timeout` to 15m
- Reduce `scan_parallel` to 4 or 8
- Enable `adaptive_timeout: true`
- Consider splitting into multiple smaller runs

---

**See also:**
- [Main README](README.md) - Overview and quick start
- [Setup Guide](SETUP.md) - Detailed setup instructions
- [Configuration Guide](CONFIGURATION.md) - Detailed configuration examples
- [Remediation Guide](REMEDIATION.md) - How to remediate discovered secrets
