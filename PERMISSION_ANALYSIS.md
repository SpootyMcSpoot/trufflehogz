# Fine-Grained PAT Permission Analysis

## API Endpoints Used by Workflow

### Workflow (Bash Script)
1. **`GET /user/memberships/orgs/{org}`** - Check if user is member of org
2. **`GET /orgs/{org}/repos?type=all&per_page=100&page={page}`** - List org repositories (paginated)
3. **`GET /repos/{owner}/{repo}`** - Get repository metadata
4. **`GET /repos/{owner}/{repo}/branches`** - List repository branches
5. **Git clone operations** - Clone repositories via HTTPS/Git protocol

### Python Script
1. **`GET /repos/{repo}`** - Get repository metadata
2. **`GET /search/issues?q=...`** - Search for existing issues
3. **`GET /repos/{repo}/labels`** - List repository labels
4. **`POST /repos/{repo}/labels`** - Create labels
5. **`POST /repos/{repo}/issues`** - Create issues

## Permission Mapping (Current README)

### Repository Permissions
- **Contents**: Read - For cloning, reading code, listing branches
- **Issues**: Read and write - For creating issues and labels
- **Metadata**: Read - Automatically granted

### Organization Permissions
- **Members**: Read - For enumerating org repos and checking membership

## Questions to Verify

1. Is "Members" (Read) the correct organization permission for:
   - `GET /user/memberships/orgs/{org}`
   - `GET /orgs/{org}/repos` (for private repos)

2. Are there any other organization permissions needed?

3. Does listing organization repositories require any additional permissions beyond:
   - Organization: Members (Read)
   - Repository: Contents (Read) + Metadata (Read)

## References

According to the official GitHub documentation at:
https://docs.github.com/en/rest/authentication/permissions-required-for-fine-grained-personal-access-tokens

Please verify the exact permission names and access levels required for the endpoints listed above.
