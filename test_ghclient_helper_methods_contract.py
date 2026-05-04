"""
Contract tests for GHClient helper methods that the scanner relies on
when reconciling issues + labels against the GitHub REST API.

Pins behaviour for currently-uncovered helpers:
  * GHClient._get_installation_for_org (lines 616-634)
  * GHClient.get_labels (855-867)
  * GHClient.create_labels_if_needed (869-880)
  * GHClient.verify_issue_exists (897-910)
  * GHClient.list_recent_issues_with_hash (818-853)
  * GHClient._req URLError handling (686-688)

These are mocked at the self.call() boundary (or at urlopen for _req
+ _get_installation_for_org) -- no real GitHub traffic.
"""
from __future__ import annotations

import io
import json
from unittest import mock
from urllib.error import HTTPError, URLError

import pytest

import trufflehog_scanner as ts
from trufflehog_scanner import GHClient


# ---------------------------------------------------------------------------
# _get_installation_for_org
# ---------------------------------------------------------------------------


def _mock_response(payload: dict):
    body = json.dumps(payload).encode("utf-8")
    resp = mock.MagicMock()
    resp.read.return_value = body
    resp.__enter__ = mock.MagicMock(return_value=resp)
    resp.__exit__ = mock.MagicMock(return_value=False)
    return resp


class TestGetInstallationForOrg:
    def test_returns_installation_id_from_api(self, monkeypatch):
        monkeypatch.setattr(ts, "JWT_AVAILABLE", True)
        monkeypatch.setattr(ts.jwt, "encode", lambda *a, **kw: "stub.jwt")
        urlopen = mock.MagicMock(return_value=_mock_response({"id": 555}))
        monkeypatch.setattr(ts, "urlopen", urlopen)

        client = GHClient(token="t", app_id="A", app_private_key="K")
        result = client._get_installation_for_org("my-org")
        assert result == 555

    def test_request_targets_orgs_installation_endpoint(self, monkeypatch):
        monkeypatch.setattr(ts, "JWT_AVAILABLE", True)
        monkeypatch.setattr(ts.jwt, "encode", lambda *a, **kw: "stub.jwt")
        urlopen = mock.MagicMock(return_value=_mock_response({"id": 1}))
        monkeypatch.setattr(ts, "urlopen", urlopen)

        client = GHClient(token="t", app_id="A", app_private_key="K")
        client._get_installation_for_org("my-org")
        req = urlopen.call_args[0][0]
        assert req.full_url.endswith("/orgs/my-org/installation")
        assert req.get_method() == "GET"

    def test_request_uses_jwt_bearer_auth(self, monkeypatch):
        monkeypatch.setattr(ts, "JWT_AVAILABLE", True)
        monkeypatch.setattr(ts.jwt, "encode", lambda *a, **kw: "abc.def.ghi")
        urlopen = mock.MagicMock(return_value=_mock_response({"id": 1}))
        monkeypatch.setattr(ts, "urlopen", urlopen)

        client = GHClient(token="t", app_id="A", app_private_key="K")
        client._get_installation_for_org("my-org")
        req = urlopen.call_args[0][0]
        assert req.headers.get("Authorization") == "Bearer abc.def.ghi"
        assert req.headers.get("Accept") == "application/vnd.github+json"
        assert req.headers.get("X-github-api-version") == "2022-11-28"

    def test_jwt_unavailable_raises_runtime_error(self, monkeypatch):
        monkeypatch.setattr(ts, "JWT_AVAILABLE", False)
        client = GHClient(token="t", app_id="A", app_private_key="K")
        with pytest.raises(RuntimeError) as exc:
            client._get_installation_for_org("my-org")
        assert "PyJWT" in str(exc.value)


# ---------------------------------------------------------------------------
# get_labels
# ---------------------------------------------------------------------------


class TestGetLabels:
    def test_returns_list_of_label_names(self):
        client = GHClient(token="t")
        with mock.patch.object(client, "call") as call:
            call.return_value = [{"name": "bug"}, {"name": "security"}]
            names = client.get_labels("owner/repo")
        assert names == ["bug", "security"]

    def test_paginates_until_short_page(self):
        client = GHClient(token="t")
        page1 = [{"name": f"l{i}"} for i in range(100)]
        page2 = [{"name": "tail"}]
        with mock.patch.object(client, "call") as call:
            call.side_effect = [page1, page2]
            names = client.get_labels("owner/repo")
        assert "l0" in names and "tail" in names
        assert call.call_count == 2
        # Page 1 vs page 2 in path
        assert "page=1" in call.call_args_list[0][0][1]
        assert "page=2" in call.call_args_list[1][0][1]

    def test_caps_pagination_at_max_pages(self):
        client = GHClient(token="t")
        full_page = [{"name": f"l{i}"} for i in range(100)]
        with mock.patch.object(client, "call") as call:
            call.return_value = full_page
            client.get_labels("owner/repo")
        # max_pages=10 guard
        assert call.call_count == 10

    def test_non_list_response_breaks(self):
        client = GHClient(token="t")
        with mock.patch.object(client, "call") as call:
            call.return_value = {"message": "Not Found"}
            names = client.get_labels("owner/repo")
        assert names == []

    def test_filters_out_empty_names(self):
        client = GHClient(token="t")
        with mock.patch.object(client, "call") as call:
            call.return_value = [{"name": ""}, {"name": "real"}, {"foo": "bar"}]
            names = client.get_labels("owner/repo")
        assert names == ["real"]


# ---------------------------------------------------------------------------
# create_labels_if_needed
# ---------------------------------------------------------------------------


class TestCreateLabelsIfNeeded:
    def test_creates_each_label_via_post(self, caplog):
        client = GHClient(token="t")
        with mock.patch.object(client, "call") as call:
            call.return_value = {}
            with caplog.at_level("INFO"):
                client.create_labels_if_needed("owner/repo", ["a", "b"])
        # POST per label
        assert call.call_count == 2
        for c, label in zip(call.call_args_list, ["a", "b"]):
            args, _ = c
            assert args[0] == "POST"
            assert args[1] == "/repos/owner/repo/labels"
            assert args[2] == {"name": label}

    def test_409_treated_as_already_exists(self):
        client = GHClient(token="t")
        err = HTTPError("u", 409, "exists", {}, None)
        with mock.patch.object(client, "call") as call:
            call.side_effect = err
            # Should NOT raise -- swallow as "already exists"
            client.create_labels_if_needed("owner/repo", ["a"])

    def test_422_treated_as_already_exists(self):
        client = GHClient(token="t")
        err = HTTPError("u", 422, "validation failed", {}, None)
        with mock.patch.object(client, "call") as call:
            call.side_effect = err
            client.create_labels_if_needed("owner/repo", ["a"])

    def test_other_http_error_logs_warning_and_continues(self, caplog):
        client = GHClient(token="t")
        err500 = HTTPError("u", 500, "server", {}, None)
        with mock.patch.object(client, "call") as call:
            call.side_effect = [err500, {}]
            with caplog.at_level("WARNING"):
                client.create_labels_if_needed("owner/repo", ["a", "b"])
        joined = " ".join(r.message for r in caplog.records)
        assert "Could not create label a" in joined
        assert "500" in joined
        # Still attempted second label after first failed
        assert call.call_count == 2


# ---------------------------------------------------------------------------
# verify_issue_exists
# ---------------------------------------------------------------------------


class TestVerifyIssueExists:
    def test_dry_run_short_circuits_true(self):
        client = GHClient(token="t", dry_run=True)
        # Even with a positive issue_number, dry_run path returns True
        assert client.verify_issue_exists("owner/repo", 123) is True

    def test_zero_issue_number_returns_false(self):
        client = GHClient(token="t", dry_run=False)
        assert client.verify_issue_exists("owner/repo", 0) is False

    def test_negative_issue_number_returns_false(self):
        client = GHClient(token="t", dry_run=False)
        assert client.verify_issue_exists("owner/repo", -1) is False

    def test_returns_true_when_issue_number_matches(self, caplog):
        client = GHClient(token="t")
        with mock.patch.object(client, "call") as call:
            call.return_value = {"number": 42, "state": "open"}
            with caplog.at_level("INFO"):
                ok = client.verify_issue_exists("owner/repo", 42)
        assert ok is True
        assert call.call_args[0] == ("GET", "/repos/owner/repo/issues/42", None)

    def test_returns_false_on_unexpected_response_shape(self, caplog):
        client = GHClient(token="t")
        with mock.patch.object(client, "call") as call:
            call.return_value = {"number": 99}  # mismatched issue number
            with caplog.at_level("WARNING"):
                ok = client.verify_issue_exists("owner/repo", 42)
        assert ok is False
        joined = " ".join(r.message for r in caplog.records)
        assert "Verification failed" in joined

    def test_returns_false_on_http_error(self, caplog):
        client = GHClient(token="t")
        err = HTTPError("u", 404, "Not Found", {}, None)
        with mock.patch.object(client, "call") as call:
            call.side_effect = err
            with caplog.at_level("ERROR"):
                ok = client.verify_issue_exists("owner/repo", 42)
        assert ok is False


# ---------------------------------------------------------------------------
# list_recent_issues_with_hash (search_recent_issues_with_hash)
# ---------------------------------------------------------------------------


class TestSearchRecentIssuesWithHash:
    def test_returns_true_when_title_and_hash_match_recent_issue(self):
        client = GHClient(token="t")
        recent = [{
            "title": "[TruffleHog] alert hash:abc123",
            "created_at": "2099-01-01T00:00:00Z",
            "state": "open",
        }]
        with mock.patch.object(client, "call") as call:
            call.return_value = recent
            result = client.search_recent_issues_with_hash(
                "owner/repo", "[TruffleHog]", "hash:abc123", days=30
            )
        assert result is True

    def test_returns_false_when_title_pattern_missing(self):
        client = GHClient(token="t")
        with mock.patch.object(client, "call") as call:
            call.return_value = [{
                "title": "unrelated",
                "created_at": "2099-01-01T00:00:00Z",
            }]
            result = client.search_recent_issues_with_hash(
                "owner/repo", "[TruffleHog]", "hash:abc123"
            )
        assert result is False

    def test_returns_false_when_hash_pattern_missing(self):
        client = GHClient(token="t")
        with mock.patch.object(client, "call") as call:
            call.return_value = [{
                "title": "[TruffleHog] something different",
                "created_at": "2099-01-01T00:00:00Z",
            }]
            result = client.search_recent_issues_with_hash(
                "owner/repo", "[TruffleHog]", "hash:abc123"
            )
        assert result is False

    def test_old_issue_outside_cutoff_ignored(self):
        client = GHClient(token="t")
        with mock.patch.object(client, "call") as call:
            call.return_value = [{
                "title": "[TruffleHog] alert hash:abc123",
                "created_at": "1999-01-01T00:00:00Z",
                "state": "closed",
            }]
            result = client.search_recent_issues_with_hash(
                "owner/repo", "[TruffleHog]", "hash:abc123", days=30
            )
        assert result is False

    def test_invalid_created_at_silently_skipped(self):
        client = GHClient(token="t")
        with mock.patch.object(client, "call") as call:
            call.return_value = [{
                "title": "[TruffleHog] x hash:abc123",
                "created_at": "not-a-date",
            }]
            # Must not raise
            result = client.search_recent_issues_with_hash(
                "owner/repo", "[TruffleHog]", "hash:abc123"
            )
        assert result is False

    def test_non_list_response_breaks_pagination(self):
        client = GHClient(token="t")
        with mock.patch.object(client, "call") as call:
            call.return_value = {"message": "API error"}
            result = client.search_recent_issues_with_hash(
                "owner/repo", "[TruffleHog]", "hash:abc123"
            )
        assert result is False
        assert call.call_count == 1

    def test_short_page_breaks_pagination(self):
        client = GHClient(token="t")
        with mock.patch.object(client, "call") as call:
            call.return_value = [{"title": "x", "created_at": ""}]
            result = client.search_recent_issues_with_hash(
                "owner/repo", "[TruffleHog]", "hash:abc"
            )
        assert result is False
        # First page returned <30 results -> stop after 1 call
        assert call.call_count == 1

    def test_http_error_logged_and_returns_false(self, caplog):
        client = GHClient(token="t")
        err = HTTPError("u", 500, "boom", {}, None)
        with mock.patch.object(client, "call") as call:
            call.side_effect = err
            with caplog.at_level("WARNING"):
                result = client.search_recent_issues_with_hash(
                    "owner/repo", "[TruffleHog]", "hash:abc"
                )
        assert result is False

    def test_searches_all_states_open_and_closed(self):
        # The function intentionally queries state=all so closed dupes
        # don't get re-opened.
        client = GHClient(token="t")
        with mock.patch.object(client, "call") as call:
            call.return_value = []
            client.search_recent_issues_with_hash(
                "owner/repo", "[TruffleHog]", "hash:abc"
            )
        assert "state=all" in call.call_args_list[0][0][1]


# ---------------------------------------------------------------------------
# _req URLError handling
# ---------------------------------------------------------------------------


class TestReqUrlError:
    def test_url_error_logged_and_reraised(self, monkeypatch, caplog):
        client = GHClient(token="t")
        err = URLError("connection refused")
        monkeypatch.setattr(ts, "urlopen", mock.MagicMock(side_effect=err))
        with caplog.at_level("WARNING"):
            with pytest.raises(URLError):
                client._req("GET", "/repos/x/y", None, attempt=1)
        joined = " ".join(r.message for r in caplog.records)
        assert "URLError" in joined
        assert "/repos/x/y" in joined
