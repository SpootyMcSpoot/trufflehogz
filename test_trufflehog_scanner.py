"""
Unit tests for trufflehog_scanner.py

Run with: pytest test_trufflehog_scanner.py -v
"""

import os
import re
import tempfile
from unittest import mock

import pytest

# Import functions to test
from trufflehog_scanner import (
    _safe_int_env,
    _severity_rank,
    deepget,
    sanitize_repo,
    extract_repo,
    find_repo,
    file_path,
    line_no,
    commit_sha,
    shortpath,
    line_link,
    normalize_detector,
    labels_for_items,
    make_finding_key,
    compose_match_string,
    is_excluded,
    iter_ndjson,
    compute_findings_hash,
    RateLimiter,
)


class TestSafeIntEnv:
    """Tests for _safe_int_env helper function."""

    def test_valid_int(self):
        with mock.patch.dict(os.environ, {"TEST_VAR": "42"}):
            assert _safe_int_env("TEST_VAR", 0) == 42

    def test_missing_env_returns_default(self):
        assert _safe_int_env("NONEXISTENT_VAR_12345", 99) == 99

    def test_invalid_int_returns_default(self):
        with mock.patch.dict(os.environ, {"TEST_VAR": "not_a_number"}):
            assert _safe_int_env("TEST_VAR", 50) == 50

    def test_empty_string_returns_default(self):
        with mock.patch.dict(os.environ, {"TEST_VAR": ""}):
            assert _safe_int_env("TEST_VAR", 25) == 25


class TestSeverityRank:
    """Tests for _severity_rank helper function."""

    def test_known_severities(self):
        assert _severity_rank("sec:low") == 0
        assert _severity_rank("sec:medium") == 1
        assert _severity_rank("sec:high") == 2
        assert _severity_rank("sec:critical") == 3

    def test_unknown_severity_defaults_to_medium(self):
        assert _severity_rank("unknown") == 1
        assert _severity_rank("") == 1


class TestDeepget:
    """Tests for deepget nested dictionary access."""

    def test_simple_path(self):
        obj = {"a": {"b": {"c": 42}}}
        assert deepget(obj, ["a", "b", "c"]) == 42

    def test_missing_key_returns_none(self):
        obj = {"a": {"b": 1}}
        assert deepget(obj, ["a", "c"]) is None

    def test_empty_path_returns_obj(self):
        obj = {"a": 1}
        assert deepget(obj, []) == obj

    def test_non_dict_in_path_returns_none(self):
        obj = {"a": [1, 2, 3]}
        assert deepget(obj, ["a", "b"]) is None


class TestSanitizeRepo:
    """Tests for sanitize_repo function."""

    def test_removes_git_suffix(self):
        assert sanitize_repo("owner/repo.git") == "owner/repo"
        assert sanitize_repo("owner/repo.GIT") == "owner/repo"

    def test_preserves_valid_repo(self):
        assert sanitize_repo("owner/repo") == "owner/repo"

    def test_empty_string(self):
        assert sanitize_repo("") == ""

    def test_no_slash_returns_as_is(self):
        assert sanitize_repo("noslash") == "noslash"

    def test_none_returns_empty(self):
        assert sanitize_repo(None) == ""


class TestExtractRepo:
    """Tests for extract_repo function."""

    def test_github_https_url(self):
        assert extract_repo("https://github.com/owner/repo") == "owner/repo"
        assert extract_repo("https://github.com/owner/repo.git") == "owner/repo"
        assert extract_repo("https://github.com/owner/repo/blob/main/file.py") == "owner/repo"

    def test_simple_owner_repo(self):
        assert extract_repo("owner/repo") == "owner/repo"

    def test_none_returns_none(self):
        assert extract_repo(None) is None

    def test_empty_returns_none(self):
        assert extract_repo("") is None

    def test_single_segment_returns_none(self):
        assert extract_repo("justowner") is None


class TestFindRepo:
    """Tests for find_repo function."""

    def test_git_source_metadata(self):
        obj = {
            "SourceMetadata": {
                "Data": {
                    "Git": {
                        "repository": "https://github.com/owner/repo"
                    }
                }
            }
        }
        assert find_repo(obj) == "owner/repo"

    def test_github_source_metadata(self):
        obj = {
            "SourceMetadata": {
                "Data": {
                    "GitHub": {
                        "repository": "owner/repo"
                    }
                }
            }
        }
        assert find_repo(obj) == "owner/repo"

    def test_github_lowercase_source_metadata(self):
        """TruffleHog uses 'Github' (lowercase h) in its output."""
        obj = {
            "SourceMetadata": {
                "Data": {
                    "Github": {
                        "repository": "https://github.com/trufflesecurity/test_keys.git"
                    }
                }
            }
        }
        assert find_repo(obj) == "trufflesecurity/test_keys"

    def test_fallback_to_url_pattern(self):
        obj = {"Raw": "found at https://github.com/found/here/blob/main/file.py"}
        assert find_repo(obj) == "found/here"

    def test_empty_obj_returns_none(self):
        assert find_repo({}) is None


class TestFilePath:
    """Tests for file_path function."""

    def test_extracts_file_from_git_metadata(self):
        obj = {"SourceMetadata": {"Data": {"Git": {"file": "src/config.py"}}}}
        assert file_path(obj) == "src/config.py"

    def test_missing_file_returns_empty(self):
        assert file_path({}) == ""


class TestLineNo:
    """Tests for line_no function."""

    def test_int_line_number(self):
        obj = {"SourceMetadata": {"Data": {"Git": {"line": 42}}}}
        assert line_no(obj) == 42

    def test_string_line_number(self):
        obj = {"SourceMetadata": {"Data": {"Git": {"line": "100"}}}}
        assert line_no(obj) == 100

    def test_missing_returns_none(self):
        assert line_no({}) is None

    def test_non_numeric_string_returns_none(self):
        obj = {"SourceMetadata": {"Data": {"Git": {"line": "abc"}}}}
        assert line_no(obj) is None


class TestCommitSha:
    """Tests for commit_sha function."""

    def test_extracts_commit(self):
        obj = {"SourceMetadata": {"Data": {"Git": {"commit": "abc123def"}}}}
        assert commit_sha(obj) == "abc123def"

    def test_missing_returns_none(self):
        assert commit_sha({}) is None


class TestShortpath:
    """Tests for shortpath function."""

    def test_short_path_unchanged(self):
        assert shortpath("src/file.py") == "src/file.py"
        assert shortpath("a/b/c") == "a/b/c"

    def test_long_path_truncated(self):
        assert shortpath("a/b/c/d/e/f.py") == ".../d/e/f.py"

    def test_empty_string(self):
        assert shortpath("") == ""

    def test_none_returns_empty(self):
        assert shortpath(None) == ""


class TestLineLink:
    """Tests for line_link function."""

    def test_full_link_with_commit(self):
        link = line_link("owner/repo", "abc123", "src/file.py", 42)
        assert link == "https://github.com/owner/repo/blob/abc123/src/file.py"
        assert "#L42" not in link  # Line anchor intentionally omitted

    def test_link_without_commit(self):
        link = line_link("owner/repo", None, "src/file.py", 42)
        assert link == "https://github.com/owner/repo/blob/HEAD/src/file.py"

    def test_commit_only_link(self):
        link = line_link("owner/repo", "abc123", "", None)
        assert link == "https://github.com/owner/repo/commit/abc123"

    def test_missing_repo_returns_empty(self):
        assert line_link("", None, "file.py", 1) == ""

    def test_url_encodes_path(self):
        link = line_link("owner/repo", "abc", "path with spaces/file.py", 1)
        assert "path%20with%20spaces" in link


class TestNormalizeDetector:
    """Tests for normalize_detector function."""

    def test_aws_detector(self):
        assert normalize_detector("AWS") == "AWS"
        assert normalize_detector("aws_access_key") == "AWS"

    def test_github_detector(self):
        assert normalize_detector("GitHub") == "GitHub"
        assert normalize_detector("github_token") == "GitHub"

    def test_slack_detector(self):
        # Any input containing "slack" matches "Slack" detector
        # (dict iteration order means Slack checked before SlackWebhook)
        assert normalize_detector("Slack Webhook URL") == "Slack"
        assert normalize_detector("slackwebhook_token") == "Slack"
        assert normalize_detector("slack_api") == "Slack"

    def test_private_key(self):
        assert normalize_detector("RSA Private Key") == "Private Key"
        assert normalize_detector("PEM file") == "Private Key"

    def test_unknown_returns_generic(self):
        assert normalize_detector("SomeUnknownDetector") == "Generic"
        assert normalize_detector("") == "Generic"


class TestLabelsForItems:
    """Tests for labels_for_items function."""

    def test_aws_finding_labels(self):
        items = [{"detector": "AWS"}]
        labels = labels_for_items(items)
        assert "sec:critical" in labels
        assert "secret:aws" in labels
        assert "needs-triage" in labels

    def test_multiple_findings_highest_severity(self):
        items = [
            {"detector": "GitHub"},  # sec:high
            {"detector": "AWS"},     # sec:critical
        ]
        labels = labels_for_items(items)
        assert "sec:critical" in labels
        assert "sec:high" in labels

    def test_extra_labels_added(self):
        items = [{"detector": "GitHub"}]
        labels = labels_for_items(items, extra=["custom-label", "another-one"])
        assert "custom-label" in labels
        assert "another-one" in labels

    def test_empty_extra_labels_ignored(self):
        items = [{"detector": "AWS"}]
        labels = labels_for_items(items, extra=["", "  ", "valid"])
        assert "valid" in labels
        assert "" not in labels


class TestMakeFindingKey:
    """Tests for make_finding_key function."""

    def test_full_key(self):
        key = make_finding_key("owner/repo", "AWS", "file.py", 42, "abc123")
        assert key == ("owner/repo", "AWS", "file.py", 42, "abc123")

    def test_none_line_becomes_minus_one(self):
        key = make_finding_key("owner/repo", "AWS", "file.py", None, "abc123")
        assert key[3] == -1

    def test_none_commit_becomes_empty(self):
        key = make_finding_key("owner/repo", "AWS", "file.py", 42, None)
        assert key[4] == ""

    def test_sanitizes_repo(self):
        key = make_finding_key("owner/repo.git", "AWS", "file.py", 1, "abc")
        assert key[0] == "owner/repo"


class TestComposeMatchString:
    """Tests for compose_match_string function."""

    def test_composes_all_fields(self):
        obj = {
            "DetectorName": "AWS",
            "Redacted": "AKIA***EXAMPLE",
            "Verified": True,
            "SourceMetadata": {
                "Data": {
                    "Git": {
                        "file": "config.py",
                        "repository": "owner/repo"
                    }
                }
            }
        }
        match_str = compose_match_string(obj)
        assert "detector=AWS" in match_str
        assert "verified=True" in match_str
        assert "file=config.py" in match_str
        assert "repo=owner/repo" in match_str
        assert "redacted=AKIA***EXAMPLE" in match_str


class TestIsExcluded:
    """Tests for is_excluded function."""

    def test_matches_pattern(self):
        # Pattern matches against compose_match_string output (detector, repo, file, redacted)
        obj = {"DetectorName": "EXAMPLE_DETECTOR", "Redacted": "test"}
        patterns = [re.compile(r"EXAMPLE")]
        assert is_excluded(obj, patterns) is True

    def test_no_match(self):
        obj = {"DetectorName": "AWS", "Redacted": "REAL_SECRET"}
        patterns = [re.compile(r"NOTFOUND")]
        assert is_excluded(obj, patterns) is False

    def test_empty_patterns(self):
        obj = {"DetectorName": "anything"}
        assert is_excluded(obj, []) is False


class TestIterNdjson:
    """Tests for iter_ndjson generator function."""

    def test_reads_ndjson_lines(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.ndjson', delete=False) as f:
            f.write('{"key": "value1"}\n')
            f.write('{"key": "value2"}\n')
            f.name
        try:
            results = list(iter_ndjson(f.name))
            assert len(results) == 2
            assert results[0]["key"] == "value1"
            assert results[1]["key"] == "value2"
        finally:
            os.unlink(f.name)

    def test_skips_invalid_json(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.ndjson', delete=False) as f:
            f.write('{"valid": true}\n')
            f.write('not valid json\n')
            f.write('{"also_valid": true}\n')
            f.name
        try:
            results = list(iter_ndjson(f.name))
            assert len(results) == 2
        finally:
            os.unlink(f.name)

    def test_verified_only_filter(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.ndjson', delete=False) as f:
            f.write('{"Verified": true, "id": 1}\n')
            f.write('{"Verified": false, "id": 2}\n')
            f.write('{"id": 3}\n')
            f.name
        try:
            results = list(iter_ndjson(f.name, verified_only=True))
            assert len(results) == 1
            assert results[0]["id"] == 1
        finally:
            os.unlink(f.name)

    def test_nonexistent_file_yields_nothing(self):
        results = list(iter_ndjson("/nonexistent/path/file.ndjson"))
        assert results == []


class TestComputeFindingsHash:
    """Tests for compute_findings_hash function."""

    def test_same_items_same_hash(self):
        items = [
            {"detector": "AWS", "file": "a.py", "line": 1, "commit": "abc"},
            {"detector": "GitHub", "file": "b.py", "line": 2, "commit": "def"},
        ]
        hash1 = compute_findings_hash(items)
        hash2 = compute_findings_hash(items)
        assert hash1 == hash2

    def test_different_items_different_hash(self):
        items1 = [{"detector": "AWS", "file": "a.py", "line": 1, "commit": "abc"}]
        items2 = [{"detector": "GitHub", "file": "b.py", "line": 2, "commit": "def"}]
        assert compute_findings_hash(items1) != compute_findings_hash(items2)

    def test_order_independent(self):
        items1 = [
            {"detector": "AWS", "file": "a.py", "line": 1, "commit": "abc"},
            {"detector": "GitHub", "file": "b.py", "line": 2, "commit": "def"},
        ]
        items2 = [
            {"detector": "GitHub", "file": "b.py", "line": 2, "commit": "def"},
            {"detector": "AWS", "file": "a.py", "line": 1, "commit": "abc"},
        ]
        assert compute_findings_hash(items1) == compute_findings_hash(items2)


class TestRateLimiter:
    """Tests for RateLimiter class."""

    def test_wait_respects_interval(self):
        limiter = RateLimiter(calls_per_sec=10.0)  # 0.1s interval
        assert limiter.interval == pytest.approx(0.1, rel=0.01)

    def test_handles_zero_rate(self):
        # Should not crash with zero rate
        limiter = RateLimiter(calls_per_sec=0)
        assert limiter.interval > 0

    def test_handles_negative_rate(self):
        # Should handle negative rate gracefully
        limiter = RateLimiter(calls_per_sec=-5.0)
        assert limiter.interval > 0


# Import GHClient for integration tests
from trufflehog_scanner import GHClient


class TestGHClient:
    """Integration tests for GHClient GitHub API wrapper."""

    def test_dry_run_mode_does_not_call_api(self):
        """Dry run mode should not make actual API calls."""
        client = GHClient(token="fake-token", dry_run=True)
        # In dry run, POST/PUT/PATCH/DELETE should return empty dict
        result = client.request("POST", "/repos/test/test/issues", {"title": "test"})
        assert result == {}

    def test_dry_run_get_returns_empty_list(self):
        """Dry run GET requests should return empty list."""
        client = GHClient(token="fake-token", dry_run=True)
        result = client.request("GET", "/repos/test/test/issues", None)
        assert result == []

    @mock.patch('trufflehog_scanner.urlopen')
    def test_successful_api_call(self, mock_urlopen):
        """Test successful API call parsing."""
        mock_response = mock.MagicMock()
        mock_response.read.return_value = b'{"id": 123, "title": "test issue"}'
        mock_response.status = 200
        mock_response.__enter__ = mock.MagicMock(return_value=mock_response)
        mock_response.__exit__ = mock.MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        client = GHClient(token="fake-token", dry_run=False)
        result = client.call("GET", "/repos/owner/repo/issues/1", None)
        
        assert result["id"] == 123
        assert result["title"] == "test issue"

    @mock.patch('trufflehog_scanner.urlopen')
    def test_http_error_raises(self, mock_urlopen):
        """Test that HTTP errors are raised after retries exhausted."""
        from urllib.error import HTTPError
        mock_urlopen.side_effect = HTTPError(
            url="https://api.github.com/test",
            code=404,
            msg="Not Found",
            hdrs={},
            fp=None
        )

        client = GHClient(token="fake-token", dry_run=False, max_retries=1)
        with pytest.raises(HTTPError):
            client.call("GET", "/repos/owner/nonexistent", None)

    @mock.patch('trufflehog_scanner.urlopen')
    def test_rate_limit_retry(self, mock_urlopen):
        """Test that rate limit (403) triggers retry with backoff."""
        from urllib.error import HTTPError
        
        # First call: rate limited, second call: success
        mock_response = mock.MagicMock()
        mock_response.read.return_value = b'{"success": true}'
        mock_response.status = 200
        mock_response.__enter__ = mock.MagicMock(return_value=mock_response)
        mock_response.__exit__ = mock.MagicMock(return_value=False)
        
        rate_limit_error = HTTPError(
            url="https://api.github.com/test",
            code=403,
            msg="Rate limited",
            hdrs={},
            fp=None
        )
        
        mock_urlopen.side_effect = [rate_limit_error, mock_response]

        client = GHClient(token="fake-token", dry_run=False, max_retries=3)
        result = client.call("GET", "/repos/owner/repo", None)
        
        assert result["success"] is True
        assert mock_urlopen.call_count == 2

    def test_search_issue_by_title_dry_run(self):
        """Test search_issue_by_title in dry run mode."""
        client = GHClient(token="fake-token", dry_run=True)
        # Dry run should return False (no issue found)
        result = client.search_issue_by_title("owner/repo", "Test Issue")
        assert result is False

    def test_search_recent_issues_dry_run(self):
        """Test search_recent_issues in dry run mode."""
        client = GHClient(token="fake-token", dry_run=True)
        result = client.search_recent_issues("owner/repo", "[TruffleHog]")
        assert result is False

    def test_search_recent_issues_with_hash_dry_run(self):
        """Test search_recent_issues_with_hash in dry run mode."""
        client = GHClient(token="fake-token", dry_run=True)
        result = client.search_recent_issues_with_hash("owner/repo", "[TruffleHog]", "abc123")
        assert result is False

    @mock.patch('trufflehog_scanner.urlopen')
    def test_create_issue_returns_url(self, mock_urlopen):
        """Test that create_issue returns the issue URL."""
        mock_response = mock.MagicMock()
        mock_response.read.return_value = b'{"html_url": "https://github.com/owner/repo/issues/42", "number": 42}'
        mock_response.status = 201
        mock_response.__enter__ = mock.MagicMock(return_value=mock_response)
        mock_response.__exit__ = mock.MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        client = GHClient(token="fake-token", dry_run=False)
        url = client.create_issue("owner/repo", "Test Title", "Test Body", ["bug"])
        
        assert url == "https://github.com/owner/repo/issues/42"

    @mock.patch('trufflehog_scanner.urlopen')
    def test_repo_meta_returns_metadata(self, mock_urlopen):
        """Test that repo_meta returns repository metadata."""
        mock_response = mock.MagicMock()
        mock_response.read.return_value = b'{"archived": false, "disabled": false, "full_name": "owner/repo"}'
        mock_response.status = 200
        mock_response.__enter__ = mock.MagicMock(return_value=mock_response)
        mock_response.__exit__ = mock.MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        client = GHClient(token="fake-token", dry_run=False)
        meta = client.repo_meta("owner/repo")
        
        assert meta["archived"] is False
        assert meta["full_name"] == "owner/repo"

    def test_repo_meta_dry_run_returns_empty(self):
        """Test that repo_meta in dry run returns empty dict."""
        client = GHClient(token="fake-token", dry_run=True)
        meta = client.repo_meta("owner/repo")
        assert meta == {}


class TestProcessRepo:
    """Integration tests for process_repo function."""

    @mock.patch.object(GHClient, 'repo_meta')
    @mock.patch.object(GHClient, 'search_issue_by_title')
    @mock.patch.object(GHClient, 'search_recent_issues_with_hash')
    @mock.patch.object(GHClient, 'create_issue')
    @mock.patch.object(GHClient, 'ensure_labels')
    def test_process_repo_creates_issue(self, mock_labels, mock_create, mock_hash_search, 
                                         mock_title_search, mock_meta):
        """Test that process_repo creates an issue when none exists."""
        from trufflehog_scanner import process_repo
        
        mock_meta.return_value = {"archived": False, "disabled": False}
        mock_title_search.return_value = False
        mock_hash_search.return_value = False
        mock_create.return_value = "https://github.com/owner/repo/issues/1"
        mock_labels.return_value = None

        client = GHClient(token="fake-token", dry_run=False)
        items = [{"detector": "AWS", "file": "config.py", "line": 10, "commit": "abc123"}]
        
        repo, created = process_repo(
            gh=client,
            repo="owner/repo",
            items=items,
            run_url="https://github.com/owner/repo/actions/runs/123",
            title_prefix="[TruffleHog]",
            extra_labels=[]
        )
        
        assert repo == "owner/repo"
        assert created is True
        mock_create.assert_called_once()

    @mock.patch.object(GHClient, 'repo_meta')
    @mock.patch.object(GHClient, 'search_issue_by_title')
    def test_process_repo_skips_existing_issue(self, mock_title_search, mock_meta):
        """Test that process_repo skips when issue already exists."""
        from trufflehog_scanner import process_repo
        
        mock_meta.return_value = {"archived": False, "disabled": False}
        mock_title_search.return_value = True  # Issue exists

        client = GHClient(token="fake-token", dry_run=False)
        items = [{"detector": "AWS", "file": "config.py", "line": 10, "commit": "abc123"}]
        
        repo, created = process_repo(
            gh=client,
            repo="owner/repo",
            items=items,
            run_url="https://github.com/owner/repo/actions/runs/123",
            title_prefix="[TruffleHog]",
            extra_labels=[]
        )
        
        assert repo == "owner/repo"
        assert created is False

    @mock.patch.object(GHClient, 'repo_meta')
    @mock.patch.object(GHClient, 'search_issue_by_title')
    @mock.patch.object(GHClient, 'search_recent_issues_with_hash')
    @mock.patch.object(GHClient, 'create_issue')
    @mock.patch.object(GHClient, 'ensure_labels')
    def test_process_repo_force_create_bypasses_dedup(self, mock_labels, mock_create, 
                                                        mock_hash_search, mock_title_search, mock_meta):
        """Test that force_create=True bypasses deduplication checks."""
        from trufflehog_scanner import process_repo
        
        mock_meta.return_value = {"archived": False, "disabled": False}
        mock_title_search.return_value = True  # Issue would normally exist
        mock_hash_search.return_value = True   # Hash would normally match
        mock_create.return_value = "https://github.com/owner/repo/issues/2"
        mock_labels.return_value = None

        client = GHClient(token="fake-token", dry_run=False)
        items = [{"detector": "AWS", "file": "config.py", "line": 10, "commit": "abc123"}]
        
        repo, created = process_repo(
            gh=client,
            repo="owner/repo",
            items=items,
            run_url="https://github.com/owner/repo/actions/runs/123",
            title_prefix="[TruffleHog]",
            extra_labels=[],
            force_create=True  # Force creation despite existing issues
        )
        
        assert repo == "owner/repo"
        assert created is True
        # Dedup checks should not be called when force_create=True
        mock_title_search.assert_not_called()
        mock_hash_search.assert_not_called()
        mock_create.assert_called_once()

    @mock.patch.object(GHClient, 'repo_meta')
    def test_process_repo_skips_archived_repo(self, mock_meta):
        """Test that process_repo skips archived repositories."""
        from trufflehog_scanner import process_repo
        
        mock_meta.return_value = {"archived": True, "disabled": False}

        client = GHClient(token="fake-token", dry_run=False)
        items = [{"detector": "AWS", "file": "config.py", "line": 10, "commit": "abc123"}]
        
        repo, created = process_repo(
            gh=client,
            repo="owner/repo",
            items=items,
            run_url="https://github.com/owner/repo/actions/runs/123",
            title_prefix="[TruffleHog]",
            extra_labels=[]
        )
        
        assert repo == "owner/repo"
        assert created is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
