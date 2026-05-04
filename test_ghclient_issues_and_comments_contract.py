"""
Contract tests for the rest of the GHClient surface used at issue creation
+ post-creation reconciliation:

  * create_issue (lines 882-895): dry-run shape, success log, HTTPError raises
  * verify_issue_exists (897-910): dry-run shortcut, non-positive number,
    matching response, mismatched response, HTTPError swallow
  * create_suppression_labels (912-927): POSTs each label, 409/422 swallowed,
    other HTTPError logged + continues for next label
  * get_suppressed_hashes (929-968): hash extraction from title, label
    encoding, multi-label aggregation, non-list/short-page break,
    HTTPError per-label swallow
  * process_comment_commands (970-1047): dry-run shortcut, label POST per
    matched command, existing-label skip, comments HTTP error swallow,
    issues HTTP error swallow, non-list/non-dict guards

Mocks at self.call() boundary -- no real HTTP.
"""
from __future__ import annotations

import io
from unittest import mock
from urllib.error import HTTPError

import pytest

import trufflehog_scanner as ts
from trufflehog_scanner import GHClient


def _http_error(code: int) -> HTTPError:
    return HTTPError(
        url="https://api.github.com/x", code=code, msg="err",
        hdrs={}, fp=io.BytesIO(b""),
    )


# ---------------------------------------------------------------------------
# create_issue
# ---------------------------------------------------------------------------


class TestCreateIssue:
    def test_dry_run_returns_stub_payload(self):
        client = GHClient(token="t", dry_run=True)
        with mock.patch.object(client, "call") as call:
            result = client.create_issue("o/r", "T", "B", ["bug"])
        # Pin: dry-run returns synthetic payload (number=0, dummy URL)
        assert result == {"number": 0, "html_url": "(dry-run)"}
        call.assert_not_called()

    def test_real_run_posts_and_returns_response(self):
        client = GHClient(token="t")
        with mock.patch.object(
            client, "call", return_value={"number": 5, "html_url": "u"}
        ) as call:
            result = client.create_issue("o/r", "T", "B", ["a"])
        assert result == {"number": 5, "html_url": "u"}
        method, path, payload = call.call_args[0]
        assert method == "POST"
        assert path == "/repos/o/r/issues"
        assert payload == {"title": "T", "body": "B", "labels": ["a"]}

    def test_omits_labels_when_none(self):
        client = GHClient(token="t")
        with mock.patch.object(
            client, "call", return_value={}
        ) as call:
            client.create_issue("o/r", "T", "B", None)
        # Pin: payload omits 'labels' key entirely when None
        payload = call.call_args[0][2]
        assert "labels" not in payload

    def test_http_error_logged_and_reraised(self, caplog):
        client = GHClient(token="t")
        with mock.patch.object(
            client, "call", side_effect=_http_error(403)
        ):
            with pytest.raises(HTTPError):
                client.create_issue("o/r", "T", "B")


# ---------------------------------------------------------------------------
# verify_issue_exists
# ---------------------------------------------------------------------------


class TestVerifyIssueExists:
    def test_dry_run_returns_true_without_call(self):
        client = GHClient(token="t", dry_run=True)
        with mock.patch.object(client, "call") as call:
            assert client.verify_issue_exists("o/r", 5) is True
        call.assert_not_called()

    def test_zero_number_returns_false(self):
        client = GHClient(token="t")
        # Pin: issue_number<=0 short-circuits to dry_run flag (False here)
        assert client.verify_issue_exists("o/r", 0) is False

    def test_negative_number_returns_false(self):
        client = GHClient(token="t")
        assert client.verify_issue_exists("o/r", -1) is False

    def test_matching_response_returns_true(self):
        client = GHClient(token="t")
        with mock.patch.object(
            client, "call",
            return_value={"number": 7, "state": "open"},
        ):
            assert client.verify_issue_exists("o/r", 7) is True

    def test_mismatched_number_returns_false(self):
        client = GHClient(token="t")
        with mock.patch.object(
            client, "call", return_value={"number": 99}
        ):
            # Pin: API returned a different issue # = verification fails
            assert client.verify_issue_exists("o/r", 7) is False

    def test_non_dict_response_returns_false(self):
        client = GHClient(token="t")
        with mock.patch.object(client, "call", return_value="garbage"):
            assert client.verify_issue_exists("o/r", 7) is False

    def test_http_error_returns_false_not_raises(self):
        client = GHClient(token="t")
        with mock.patch.object(
            client, "call", side_effect=_http_error(404)
        ):
            # Pin: 404 on verify = false (issue gone), don't bubble
            assert client.verify_issue_exists("o/r", 7) is False


# ---------------------------------------------------------------------------
# create_suppression_labels
# ---------------------------------------------------------------------------


class TestCreateSuppressionLabels:
    def test_posts_one_label_per_definition(self):
        client = GHClient(token="t")
        with mock.patch.object(
            client, "call", return_value={}
        ) as call:
            client.create_suppression_labels("o/r")
        # Pin: one POST per SUPPRESSION_LABELS entry
        assert call.call_count == len(ts.SUPPRESSION_LABELS)
        # All POSTs target labels endpoint with name+color+description
        for args, _ in call.call_args_list:
            method, path, payload = args
            assert method == "POST"
            assert path == "/repos/o/r/labels"
            assert "name" in payload
            assert "color" in payload
            assert "description" in payload

    @pytest.mark.parametrize("code", [409, 422])
    def test_existing_label_codes_swallowed(self, code):
        client = GHClient(token="t")
        # Pin: 409 (already exists) and 422 (validation) both treated as no-op
        with mock.patch.object(
            client, "call", side_effect=_http_error(code)
        ):
            # Should not raise
            client.create_suppression_labels("o/r")

    def test_other_http_errors_logged_and_continue(self, caplog):
        client = GHClient(token="t")
        # First label hits 500, rest pass through 409
        responses = [_http_error(500)] + [_http_error(409)] * (
            len(ts.SUPPRESSION_LABELS) - 1
        )
        with mock.patch.object(
            client, "call", side_effect=responses
        ) as call:
            client.create_suppression_labels("o/r")
        # Pin: 500 doesn't abort the loop; remaining labels still attempted
        assert call.call_count == len(ts.SUPPRESSION_LABELS)


# ---------------------------------------------------------------------------
# get_suppressed_hashes
# ---------------------------------------------------------------------------


class TestGetSuppressedHashes:
    def test_extracts_hash_from_matching_titles(self):
        client = GHClient(token="t")
        page = [
            {"title": "[TruffleHog] Secrets scan report (deadbeef)"},
            {"title": "[TruffleHog] Secrets scan report (cafef00d)"},
            {"title": "Other issue"},  # no prefix
        ]
        # Each suppression label gets its own list call -- return same page
        # for all, but only hashes once per dict
        with mock.patch.object(client, "call", return_value=page):
            result = client.get_suppressed_hashes("o/r", "[TruffleHog]")
        # Pin: 8-char hex hash extracted from title parens
        assert "deadbeef" in result
        assert "cafef00d" in result

    def test_returns_empty_when_no_matches(self):
        client = GHClient(token="t")
        with mock.patch.object(client, "call", return_value=[]):
            assert client.get_suppressed_hashes("o/r") == set()

    def test_label_name_url_encoded(self):
        client = GHClient(token="t")
        captured_paths = []

        def fake_call(method, path, payload):
            captured_paths.append(path)
            return []

        with mock.patch.object(client, "call", side_effect=fake_call):
            client.get_suppressed_hashes("o/r")
        # Pin: SUPPRESSION_LABEL_NAMES include ':' which urlquote escapes to %3A
        assert any("%3A" in p for p in captured_paths)
        # state=all so closed issues count for suppression
        assert all("state=all" in p for p in captured_paths)

    def test_http_error_per_label_swallowed(self):
        client = GHClient(token="t")
        # Some labels error, others succeed -- aggregate still returns
        with mock.patch.object(
            client, "call", side_effect=_http_error(403)
        ):
            # Pin: HTTPError per label must NOT raise; degrade to empty set
            assert client.get_suppressed_hashes("o/r") == set()

    def test_non_list_response_breaks_inner_loop(self):
        client = GHClient(token="t")
        # Return non-list to trigger break in page loop
        with mock.patch.object(
            client, "call", return_value={"unexpected": "shape"}
        ):
            # Pin: non-list response aborts page iteration cleanly
            assert client.get_suppressed_hashes("o/r") == set()

    def test_short_page_skips_pagination(self):
        client = GHClient(token="t")
        page1 = [{"title": "x"}]
        with mock.patch.object(
            client, "call", return_value=page1
        ) as call:
            client.get_suppressed_hashes("o/r")
        # Pin: 1 call per label (page2 skipped because len(page1) < 30)
        assert call.call_count == len(ts.SUPPRESSION_LABEL_NAMES)


# ---------------------------------------------------------------------------
# process_comment_commands
# ---------------------------------------------------------------------------


class TestProcessCommentCommands:
    def test_dry_run_returns_zero_without_calling(self):
        client = GHClient(token="t", dry_run=True)
        with mock.patch.object(client, "call") as call:
            assert client.process_comment_commands("o/r") == 0
        call.assert_not_called()

    def test_applies_label_for_matching_comment(self):
        client = GHClient(token="t")
        issues_page = [
            {
                "title": "[TruffleHog] Secrets scan report (abc12345)",
                "number": 7,
                "labels": [],
            }
        ]
        comments = [
            {"body": "/trufflehog false-positive\nplease ignore"}
        ]

        # 3 calls expected: list issues (page 1), get comments, POST label
        with mock.patch.object(
            client, "call",
            side_effect=[issues_page, comments, {}],
        ) as call:
            result = client.process_comment_commands("o/r")
        assert result == 1
        # Pin: third call is the POST /labels
        post_call = call.call_args_list[2]
        method, path, payload = post_call[0]
        assert method == "POST"
        assert "/labels" in path
        assert payload["labels"] == ["trufflehog:false-positive"]

    def test_skips_comment_when_label_already_present(self):
        client = GHClient(token="t")
        # Issue already labeled trufflehog:false-positive
        issues_page = [
            {
                "title": "[TruffleHog] X",
                "number": 7,
                "labels": [{"name": "trufflehog:false-positive"}],
            }
        ]
        comments = [{"body": "/trufflehog false-positive"}]
        with mock.patch.object(
            client, "call",
            side_effect=[issues_page, comments],
        ) as call:
            result = client.process_comment_commands("o/r")
        # Pin: no third call (POST label) when label already there
        assert result == 0
        assert call.call_count == 2

    def test_label_post_http_error_swallowed_count_unchanged(self):
        client = GHClient(token="t")
        issues_page = [
            {"title": "[TruffleHog] X", "number": 7, "labels": []}
        ]
        comments = [{"body": "/trufflehog wont-fix"}]

        with mock.patch.object(
            client, "call",
            side_effect=[issues_page, comments, _http_error(403)],
        ):
            # Pin: POST /labels failure logged + continue; applied stays 0
            assert client.process_comment_commands("o/r") == 0

    def test_comments_http_error_swallowed_continues(self):
        client = GHClient(token="t")
        issues_page = [
            {"title": "[TruffleHog] X", "number": 7, "labels": []}
        ]
        with mock.patch.object(
            client, "call",
            side_effect=[issues_page, _http_error(404)],
        ):
            # Pin: comments fetch failure on one issue must NOT crash run
            assert client.process_comment_commands("o/r") == 0

    def test_top_level_http_error_swallowed_returns_zero(self):
        client = GHClient(token="t")
        with mock.patch.object(
            client, "call", side_effect=_http_error(500)
        ):
            assert client.process_comment_commands("o/r") == 0

    def test_skips_issues_without_title_prefix(self):
        client = GHClient(token="t")
        issues_page = [
            {"title": "Unrelated issue", "number": 7, "labels": []}
        ]
        with mock.patch.object(
            client, "call", return_value=issues_page
        ) as call:
            assert client.process_comment_commands("o/r") == 0
        # Only the issues-list calls (page 1 + page 2 if full); since page1
        # has 1 entry < 30, page 2 is skipped
        assert call.call_count == 1

    def test_skips_issue_without_number(self):
        client = GHClient(token="t")
        issues_page = [
            {"title": "[TruffleHog] X", "labels": []}  # no number
        ]
        with mock.patch.object(
            client, "call", return_value=issues_page
        ) as call:
            assert client.process_comment_commands("o/r") == 0
        # Pin: missing number -> continue (no comments fetch attempted)
        assert call.call_count == 1

    def test_non_list_issues_response_breaks(self):
        client = GHClient(token="t")
        with mock.patch.object(
            client, "call", return_value={"unexpected": True}
        ):
            assert client.process_comment_commands("o/r") == 0

    def test_non_dict_comment_skipped(self):
        client = GHClient(token="t")
        issues_page = [
            {"title": "[TruffleHog] X", "number": 7, "labels": []}
        ]
        comments = ["garbage", None, {"body": "/trufflehog remediated"}]
        with mock.patch.object(
            client, "call",
            side_effect=[issues_page, comments, {}],
        ):
            # Pin: garbage entries skipped, valid one still processed
            assert client.process_comment_commands("o/r") == 1
