# Remediating Discovered Secrets

When TruffleHog discovers secrets in your repository, follow these steps to properly remediate them.

## Table of Contents
- [1. Immediate Response (Critical: Do This First)](#1-immediate-response-critical-do-this-first)
- [2. Remove Secrets from Current Files](#2-remove-secrets-from-current-files)
- [3. Purge Secrets from Git History](#3-purge-secrets-from-git-history)
- [4. Force Push and Notify Team](#4-force-push-and-notify-team)
- [5. Post-Remediation Steps](#5-post-remediation-steps)
- [Important Considerations](#important-considerations)
- [Alternative: Secrets Management](#alternative-secrets-management)

## 1. Immediate Response (Critical: Do This First)

**Rotate or Revoke Credentials Immediately**
- **Before** cleaning git history, assume the secret is already compromised
- Rotate API keys, passwords, tokens, or certificates
- Revoke the compromised credentials in the service provider's dashboard
- Generate new credentials and store them securely (use a secrets manager)

**Examples:**
- AWS: Deactivate and delete the IAM access key, create a new one
- GitHub: Revoke the personal access token, generate a new one
- Database passwords: Change the password immediately
- API keys: Regenerate the key in the provider's dashboard

## 2. Remove Secrets from Current Files

Remove the secret from your current working tree:

```bash
# Edit the file and remove the secret
vim path/to/file-with-secret.py

# Or replace the secret with a placeholder/environment variable
sed -i 's/sk-live-abc123xyz/os.getenv("API_KEY")/g' config.py

# Commit the change
git add path/to/file-with-secret.py
git commit -m "Remove hardcoded secret, use environment variable"
```

## 3. Purge Secrets from Git History

**CRITICAL**: Rewriting git history is a destructive operation. Coordinate with your team and ensure everyone is aware.

Choose the appropriate method based on when the secret was committed:

### For Recent Commits (Simplest - Use This When Possible)

If the secret was committed recently (last few commits), use interactive rebase:

```bash
# View recent commits to find the one with the secret
git log --oneline -10

# Interactive rebase to edit/remove commits
# Replace N with number of commits to go back
git rebase -i HEAD~N

# In the editor, change 'pick' to 'drop' for commits to remove
# Or change to 'edit' to modify the commit
# Save and close

# If you chose 'edit', make your changes now:
vim path/to/file-with-secret.py
git add path/to/file-with-secret.py
git rebase --continue

# Force push to remote (see step 4 below)
```

**Alternative for very recent commits**: If the secret is only in the last unpushed commit, simply amend:

```bash
# Fix the file
vim path/to/file-with-secret.py
git add path/to/file-with-secret.py

# Amend the last commit
git commit --amend --no-edit
```

### For Old or Complex History (Industry Standard: git-filter-repo)

For secrets deep in history or across multiple branches, use [git-filter-repo](https://github.com/newren/git-filter-repo) - the official Git-recommended tool that replaced git-filter-branch.

**Installation:**
```bash
# macOS
brew install git-filter-repo

# Linux (Debian/Ubuntu)
sudo apt-get install git-filter-repo

# Or via pip
pip install git-filter-repo
```

**Usage:**
```bash
# ALWAYS create a backup first
git clone https://github.com/org/repo.git repo-backup
cd repo

# Method 1: Remove a specific file from all history
git filter-repo --path path/to/secret-file.py --invert-paths

# Method 2: Replace text across all history (recommended for secrets in code)
echo 'sk-live-abc123xyz==>REDACTED' > /tmp/replacements.txt
git filter-repo --replace-text /tmp/replacements.txt

# Method 3: Multiple text replacements
cat > /tmp/replacements.txt <<EOF
sk-live-abc123xyz==>REDACTED
ghp_OldToken123456789==>REDACTED
AKIA1234EXAMPLEKEY==>REDACTED
EOF
git filter-repo --replace-text /tmp/replacements.txt

# Verify the secret is gone
git log --all --oneline --source -S 'sk-live-abc123xyz'
# Should return no results
```

**Why git-filter-repo?**
- Official replacement for git-filter-branch (deprecated since Git 2.38)
- 10-100x faster than git-filter-branch
- Safer: catches common mistakes and pitfalls
- Better for large repositories
- Actively maintained and recommended by Git developers

## 4. Force Push and Notify Team

**WARNING**: Force pushing rewrites history. Ensure all team members are aware and prepared.

```bash
# Force push to remote (this will rewrite history on remote)
git push origin --force --all
git push origin --force --tags

# Notify all team members to re-clone or reset their local copies
```

**Team members should:**
```bash
# Option 1: Delete and re-clone (safest)
cd ..
rm -rf old-repo
git clone https://github.com/org/repo.git

# Option 2: Reset local repo (be careful, loses local changes)
git fetch origin
git reset --hard origin/main  # or your branch name
git clean -fdx
```

## 5. Post-Remediation Steps

After cleaning git history:

1. **Verify the secret is gone**: Run TruffleHog again on the cleaned repo
   ```bash
   docker run --rm -v "$PWD:/work" -w /work \
     ghcr.io/trufflesecurity/trufflehog:3.90.6 \
     git file:///work --only-verified
   ```

2. **Invalidate GitHub's cache** (for public repos): Contact GitHub Support to purge cached refs

3. **Update protection rules**:
   - Add the file/pattern to `.gitignore` if it shouldn't be tracked
   - Set up pre-commit hooks to prevent future secret commits
   - Enable GitHub secret scanning and push protection

4. **Add prevention measures**:
   ```bash
   # Install pre-commit hook with TruffleHog
   pip install pre-commit

   # Create .pre-commit-config.yaml
   cat > .pre-commit-config.yaml <<EOF
   repos:
     - repo: https://github.com/trufflesecurity/trufflehog
       rev: v3.90.6
       hooks:
         - id: trufflehog
           name: TruffleHog Secret Scan
           entry: trufflehog git file://. --since-commit HEAD --only-verified --fail
   EOF

   # Install the hook
   pre-commit install
   ```

5. **Monitor for exposure**:
   - Check if the secret was exposed in any forks
   - Monitor audit logs for unauthorized usage
   - Review access logs for suspicious activity

## Important Considerations

**WARNING - Public Repositories:**
- Once pushed to a public repo, assume the secret was harvested by bots
- Secrets can persist in forks even after you clean your history
- Contact GitHub Support to purge cached views of commits

**WARNING - Private Repositories:**
- Secrets may still be visible to anyone who had access
- Check who cloned the repo while the secret was exposed
- Consider if any CI/CD logs or artifacts contain the secret

**WARNING - Large Repositories:**
- History rewriting can take significant time (hours for large repos)
- Test the process on a mirror/backup first
- git-filter-repo is optimized for large repositories (10-100x faster than legacy tools)

**WARNING - Protected Branches:**
- Temporarily disable branch protection rules to force push
- Re-enable protection immediately after pushing

## Alternative: Secrets Management

To prevent secrets from entering git in the first place:

```bash
# Use environment variables
export DATABASE_PASSWORD="secret"
python app.py

# Use a .env file (add to .gitignore)
echo "DATABASE_PASSWORD=secret" > .env
echo ".env" >> .gitignore

# Use a secrets manager
# AWS Secrets Manager, HashiCorp Vault, Azure Key Vault, etc.
```

**Best practices:**
- Store secrets in environment variables or secrets managers
- Use GitHub Secrets for Actions workflows
- Never commit `.env` files (add to `.gitignore`)
- Use placeholder values in example/template files
- Enable GitHub's push protection for secret scanning

---

**See also:**
- [Main README](README.md) - Overview and quick start
- [Setup Guide](SETUP.md) - Detailed setup instructions
- [Configuration Guide](CONFIGURATION.md) - Detailed configuration examples
- [Troubleshooting](TROUBLESHOOTING.md) - Performance tuning and troubleshooting
