# Test Fixtures and Intentional Secrets

## Overview

This repository contains **intentional test secrets** used to validate TruffleHog's detection capabilities. These are NOT actual production credentials and exist solely for testing purposes.

**Important**: All secrets in this repository are fake test data designed to trigger TruffleHog detectors. They have never been valid and pose no security risk.

## Purpose

These test fixtures serve several critical functions:

1. **Validation**: Verify that TruffleHog correctly detects common secret patterns
2. **Testing**: Ensure the workflow and Python post-processor handle findings correctly
3. **Documentation**: Provide examples of what TruffleHog can detect
4. **Development**: Allow contributors to test changes without needing real secrets

## Test Data Files

### Configuration Files

| File | Type | Purpose | Secrets Included |
|------|------|---------|-----------------|
| `.env.example` | Environment variables | Example environment configuration | AWS keys, GitHub tokens, API keys |
| `config/database.yml` | YAML | Database connection examples | PostgreSQL passwords, Redis passwords |
| `docker-compose.yml` | Docker Compose | Multi-service application example | Database URLs, AWS keys, API tokens |
| `src/config.js` | JavaScript | Application configuration | API keys, database credentials |
| `terraform/main.tf` | Terraform | Infrastructure as Code example | AWS credentials, GitHub tokens, DB passwords |
| `test/test-config.json` | JSON | Test configuration | Complete credentials set for multiple services |

### Key Files

| File | Type | Purpose | Secrets Included |
|------|------|---------|-----------------|
| `keys/id_rsa` | SSH Private Key | SSH key detection test | Partial RSA private key (intentionally incomplete) |

### Scripts

| File | Type | Purpose | Secrets Included |
|------|------|---------|-----------------|
| `scripts/deploy.py` | Python | Deployment script example | API tokens, database URLs |

## Secret Types Included

This repository intentionally contains test examples of the following secret types:

### Cloud Providers
- **AWS**: Access Key IDs, Secret Access Keys (example format: `AKIAIOSFODNN7EXAMPLE`)
- **Google Cloud**: API keys, service account credentials
- **Azure**: Connection strings, storage account keys

### Version Control
- **GitHub**: Personal access tokens (ghp_*, gho_* formats)
- **GitLab**: Access tokens

### Databases
- **PostgreSQL**: Connection strings with embedded passwords
- **Redis**: AUTH passwords
- **MongoDB**: Connection URIs
- **MySQL**: Database URLs with credentials

### Third-Party Services
- **SendGrid**: API keys
- **Stripe**: Publishable and secret keys
- **Slack**: Bot tokens, webhook URLs
- **Twilio**: Auth tokens, Account SIDs
- **Mailchimp**: API keys
- **Datadog**: API keys

### Encryption & Signing
- **JWT**: Secrets for token signing
- **SSH**: Private keys (RSA format)
- **Generic**: API keys, access tokens

## File-by-File Breakdown

### `.env.example`
**Location**: Root directory
**Format**: Shell environment variables

**Contains**:
```env
AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE
AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
GITHUB_TOKEN=ghp_exampleTokenString1234567890abcdefghi
DATABASE_URL=postgresql://user:password@localhost:5432/database
```

**Status**: OK - Clearly marked as examples in filename

---

### `config/database.yml`
**Location**: config/
**Format**: YAML

**Contains**:
- Production PostgreSQL password: `Pr0duct!0n_DBp@ssw0rd_2024`
- Staging PostgreSQL password: `St@g1ng_P@ssw0rd_123`
- Redis passwords with special characters

**Status**: NEEDS ATTENTION - **Should add `# TEST DATA` comment at top of file**

---

### `docker-compose.yml`
**Location**: Root directory
**Format**: Docker Compose YAML

**Contains**:
- PostgreSQL connection URLs with embedded credentials
- AWS access keys
- Stripe keys (test mode: `pk_test_*` and `sk_test_*`)
- Slack bot token
- JWT secret
- SendGrid API key

**Status**: NEEDS ATTENTION - **Should add `# TEST DATA` comment at top of file**

---

### `src/config.js`
**Location**: src/
**Format**: JavaScript

**Contains**:
- Database connection strings
- API keys for various services
- JWT secrets
- Third-party service credentials

**Status**: NEEDS ATTENTION - **Should add `// TEST DATA` comment at top of file**

---

### `terraform/main.tf`
**Location**: terraform/
**Format**: Terraform HCL

**Contains**:
- AWS credentials in provider block (hardcoded - bad practice example)
- Database password as variable default
- GitHub token as default variable

**Status**: NEEDS ATTENTION - **Should add `# TEST DATA` comment at top of file**

---

### `test/test-config.json`
**Location**: test/
**Format**: JSON

**Contains**:
- Complete credentials set for multiple services
- Database URLs
- API keys
- Service tokens

**Status**: OK - Clear from directory name (`test/`) that this is test data

---

### `keys/id_rsa`
**Location**: keys/
**Format**: SSH Private Key (PEM)

**Contains**:
- Partial RSA private key (intentionally incomplete to avoid false positives)

**Status**: NEEDS ATTENTION - **Should add comment at top explaining this is test data**

---

### `scripts/deploy.py`
**Location**: scripts/
**Format**: Python

**Contains**:
- Hardcoded API tokens
- Database connection URLs
- Service credentials

**Status**: NEEDS ATTENTION - **Should add `# TEST DATA` comment at top of file**

## Recommendations for Clarity

Files containing test secrets should include a clearly visible header comment noting they are test data. For example:

```
# ===================================================================
# TEST DATA - NOT REAL CREDENTIALS
# ===================================================================
# This file contains intentional fake secrets for TruffleHog testing.
# These credentials have never been valid and pose no security risk.
# See TEST_FIXTURES.md for more information.
# ===================================================================
```

## Verification

You can verify these are test fixtures by:

1. **AWS Keys**: Format matches AWS but values are from AWS documentation examples
2. **GitHub Tokens**: Random character sequences in correct format but never issued
3. **Database Passwords**: Complex but never used in production
4. **API Keys**: Follow service patterns but are randomized/examples

## Real Secrets Protection

While this repository contains intentional test secrets, **never commit real secrets**:

1. Use environment variables for real credentials
2. Store sensitive data in GitHub Secrets for workflows
3. Use secret management services (AWS Secrets Manager, HashiCorp Vault, etc.)
4. Add `.env` (without `.example`) to `.gitignore`
5. Use pre-commit hooks to prevent accidental commits
6. Rotate credentials immediately if accidentally committed

## Testing Workflow

### Self-Scan Workflow

The **self-scan** workflow (`Actions → Self-Scan Test → Run workflow`) runs on every push and validates end-to-end:
- Scans this repository's `test/fixtures/` test secrets
- Asserts a minimum of **20 findings** are detected
- Runs the full 4-job pipeline (plan → scan → summarize → workflow-summary)

### Unit Tests

The scanner has **122 unit tests** covering severity classification, label generation, hash deduplication, NDJSON loading, issue body generation, summary output, and end-to-end processing:

```bash
python3 -m pytest test_trufflehog_scanner.py -v
```

### Expectations

When testing TruffleHog against this repository:

1. **Expected Behavior**: TruffleHog should detect 20+ findings (the self-scan workflow validates ≥ 20)
2. **Verified Secrets**: Some secrets may show as "verified=false" (expected for fake data)
3. **Detectors Triggered**: AWS, GitHub, PostgreSQL, Slack, Stripe, JWT, SSH, and more
4. **False Positives**: Use `.github/trufflehog/false_positives.txt` to suppress known test data

## Contributing

When adding new test fixtures:

1. **Document here**: Add to this file with clear description
2. **Never use real secrets**: Generate fake data in correct format
3. **Add comments**: Include `TEST DATA` header in new files
4. **Test detection**: Verify TruffleHog detects the new secret type
5. **Update suppressions**: Add to false_positives.txt if needed

## Security Note

If you discover what appears to be a **real** secret (not listed here as test data):

1. **Do NOT open a public issue** (would expose the secret)
2. Report privately to repository maintainers
3. Follow responsible disclosure practices
4. Assume it was a mistake and needs immediate rotation

---

**Last Updated**: 2026-04-16
**Maintained By**: TruffleHogz Contributors

## References

- [TruffleHog Detectors](https://github.com/trufflesecurity/trufflehog#detectors)
- [AWS Credential Format](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_credentials_access-keys.html)
- [GitHub Token Formats](https://github.blog/2021-04-05-behind-githubs-new-authentication-token-formats/)
