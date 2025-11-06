# Secrets Remediation Guide

How to remediate discovered secrets in your repositories.

## Quick Steps

1. **Rotate credentials immediately** (assume compromised)
2. Remove from current files
3. Purge from git history
4. Force push and notify team
5. Add prevention measures

## 1. Rotate Credentials (Do This First!)

**Before** cleaning history, rotate/revoke the secret.

**Examples**:
- **AWS**: Deactivate IAM key, create new one
- **GitHub**: Revoke token, generate new one
- **Database**: Change password
- **API keys**: Regenerate in provider dashboard

## 2. Remove from Current Files

```bash
# Edit file
vim path/to/file-with-secret.py

# Or replace inline
sed -i 's/sk-live-abc123xyz/os.getenv("API_KEY")/g' config.py

# Commit
git add path/to/file-with-secret.py
git commit -m "Remove hardcoded secret, use environment variable"
```

## 3. Purge from Git History

### Recent Commits (Interactive Rebase)

```bash
# View recent commits
git log --oneline -10

# Interactive rebase (replace N with number of commits)
git rebase -i HEAD~N

# In editor: change 'pick' to 'drop' or 'edit'
# If 'edit': make changes, then
git add path/to/file-with-secret.py
git rebase --continue
```

**Very recent**: Amend last commit if unpushed
```bash
vim path/to/file-with-secret.py
git add path/to/file-with-secret.py
git commit --amend --no-edit
```

### Old Commits (git-filter-repo)

Official Git-recommended tool (10-100x faster than git-filter-branch).

**Install**:
```bash
brew install git-filter-repo           # macOS
sudo apt-get install git-filter-repo   # Linux
pip install git-filter-repo            # pip
```

**Usage**:
```bash
# ALWAYS backup first
git clone https://github.com/org/repo.git repo-backup
cd repo

# Remove specific file
git filter-repo --path path/to/secret-file.py --invert-paths

# Replace text (recommended)
echo 'sk-live-abc123xyz==>REDACTED' > /tmp/replacements.txt
git filter-repo --replace-text /tmp/replacements.txt

# Multiple replacements
cat > /tmp/replacements.txt <<EOF
sk-live-abc123xyz==>REDACTED
ghp_OldToken123456789==>REDACTED
AKIA1234EXAMPLEKEY==>REDACTED
EOF
git filter-repo --replace-text /tmp/replacements.txt

# Verify
git log --all --oneline --source -S 'sk-live-abc123xyz'  # Should be empty
```

## 4. Force Push

**WARNING**: Rewrites history. Coordinate with team first.

```bash
# Force push
git push origin --force --all
git push origin --force --tags
```

**Team members must**:
```bash
# Option 1: Re-clone (safest)
cd .. && rm -rf old-repo
git clone https://github.com/org/repo.git

# Option 2: Hard reset (loses local changes)
git fetch origin
git reset --hard origin/main
git clean -fdx
```

## 5. Post-Remediation

**Verify**:
```bash
docker run --rm -v "$PWD:/work" -w /work \
  ghcr.io/trufflesecurity/trufflehog:3.90.6 \
  git file:///work --only-verified
```

**Prevent future leaks**:
```bash
# Install pre-commit hook
pip install pre-commit

cat > .pre-commit-config.yaml <<EOF
repos:
  - repo: https://github.com/trufflesecurity/trufflehog
    rev: v3.90.6
    hooks:
      - id: trufflehog
        name: TruffleHog Secret Scan
        entry: trufflehog git file://. --since-commit HEAD --only-verified --fail
EOF

pre-commit install
```

**Additional steps**:
- Add file/pattern to `.gitignore`
- Enable GitHub secret scanning + push protection
- Contact GitHub Support to purge cached refs (public repos)
- Monitor audit logs for unauthorized usage
- Check forks for leaked secrets

## Important Warnings

**Public Repos**: Assume secret was harvested by bots. Secrets persist in forks.

**Private Repos**: Visible to anyone with prior access. Check who cloned during exposure.

**Large Repos**: History rewriting takes time. Test on backup first.

**Protected Branches**: Temporarily disable protection to force push. Re-enable immediately.

## Secrets Management Best Practices

```bash
# Environment variables
export DATABASE_PASSWORD="secret"

# .env file (add to .gitignore)
echo "DATABASE_PASSWORD=secret" > .env
echo ".env" >> .gitignore

# Secrets managers
# AWS Secrets Manager, HashiCorp Vault, Azure Key Vault
```

**Best practices**:
- Never commit secrets
- Use environment variables or secrets managers
- Use GitHub Secrets for Actions
- Never commit `.env` files
- Enable GitHub push protection

---

**See also**:
- [README.md](README.md) - Overview
- [SETUP.md](SETUP.md) - Setup guide
- [CONFIGURATION.md](CONFIGURATION.md) - Configuration
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) - Performance tuning
