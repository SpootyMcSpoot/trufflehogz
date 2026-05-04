"""Contract pin: setup_logging + get_auth_token + parse_args.

Existing coverage:
  * test_trufflehog_scanner.py -- pure helpers (sanitize_repo, etc.),
    GHClient + multi-org auth, build_issue_body, write_markdown_summary.
  * test_io_helpers_contract.py (PR #65) -- ndjson_stats,
    load_exclude_regexes, load_errors_ndjson, sanitized preview.

What's STILL unpinned and matters in production:

  * ``setup_logging`` -- single-line stdlib wrapper but the format
    string is what the SRE log-aggregator parses. Drift in the
    record fields silently breaks the Loki query that feeds the
    Trufflehog dashboard.
  * ``get_auth_token`` -- the auth ladder: GitHub App env triple ->
    PAT -> GH_TOKEN/GITHUB_TOKEN -> raise. Drift in priority order
    OR in the raised-error message would silently fall back to a
    less-privileged token (App > PAT > Action default), changing
    rate-limit budgets and dedup behaviour.
  * ``parse_args`` -- the CLI surface contract. Drift in any default
    breaks the GHA workflow that invokes the scanner via cron.
    Pin defaults, types, and store_true/false action modes.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from unittest import mock

import pytest


# ---------------------------------------------------------------------------
# setup_logging
# ---------------------------------------------------------------------------


class TestSetupLogging:
    def test_default_level_is_info(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Pin: ``setup_logging()`` (no arg) MUST default to INFO. Drift
        # to DEBUG would leak per-finding details to public CI logs.
        from trufflehog_scanner import setup_logging

        captured: dict = {}

        def _capture(level=None, format=None, **kwargs):
            captured["level"] = level
            captured["format"] = format

        monkeypatch.setattr(logging, "basicConfig", _capture)
        setup_logging()
        assert captured["level"] == logging.INFO

    def test_level_string_resolved_case_insensitive(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Pin: ``setup_logging("debug")`` lower-case MUST resolve to
        # logging.DEBUG via getattr(...upper()). A regression that
        # only honored exact-case input would silently fall back to
        # the default.
        from trufflehog_scanner import setup_logging

        captured: dict = {}

        def _capture(level=None, format=None, **kwargs):
            captured["level"] = level

        monkeypatch.setattr(logging, "basicConfig", _capture)
        setup_logging("debug")
        assert captured["level"] == logging.DEBUG

    def test_unknown_level_falls_back_to_info(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Pin: a typo'd LOG_LEVEL env (``--log-level VERBOSE``) MUST
        # fall back to INFO -- not crash the scanner.
        from trufflehog_scanner import setup_logging

        captured: dict = {}

        def _capture(level=None, format=None, **kwargs):
            captured["level"] = level

        monkeypatch.setattr(logging, "basicConfig", _capture)
        setup_logging("VERBOSE")  # not a real level
        assert captured["level"] == logging.INFO

    def test_format_string_carries_required_fields(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Pin: the Loki dashboard parses ``%(asctime)s``,
        # ``%(levelname)s``, ``%(threadName)s``, and ``%(funcName)s``
        # out of the line. Drift breaks the panel.
        from trufflehog_scanner import setup_logging

        captured: dict = {}

        def _capture(level=None, format=None, **kwargs):
            captured["format"] = format

        monkeypatch.setattr(logging, "basicConfig", _capture)
        setup_logging("INFO")
        fmt = captured["format"]
        assert "%(asctime)s" in fmt
        assert "%(levelname)s" in fmt
        assert "%(threadName)s" in fmt
        assert "%(funcName)s" in fmt
        assert "%(message)s" in fmt


# ---------------------------------------------------------------------------
# get_auth_token -- ladder: App -> PAT -> GH_TOKEN/GITHUB_TOKEN -> raise
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch):
    """Clear all auth-related env vars so each test starts deterministic."""
    for k in (
        "GH_APP_ID",
        "GH_APP_PRIVATE_KEY",
        "GH_APP_INSTALLATION_ID",
        "GH_PAT",
        "GH_TOKEN",
        "GITHUB_TOKEN",
    ):
        monkeypatch.delenv(k, raising=False)


class TestGetAuthToken:
    def test_no_credentials_raises_value_error(self, clean_env) -> None:
        # Pin: with NO auth env vars set, the function MUST raise
        # ValueError with a descriptive message -- NOT silently
        # return "" / None (which would leak unauthenticated calls).
        from trufflehog_scanner import get_auth_token

        with pytest.raises(ValueError, match="No authentication method available"):
            get_auth_token()

    def test_pat_returned_when_no_app_creds(
        self, clean_env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Pin: GH_PAT is the second priority after the GitHub App
        # triple. With no App vars, PAT MUST win over GH_TOKEN.
        monkeypatch.setenv("GH_PAT", "pat-secret")
        monkeypatch.setenv("GH_TOKEN", "actions-secret")
        from trufflehog_scanner import get_auth_token

        assert get_auth_token() == "pat-secret"

    def test_app_creds_take_priority_over_pat(
        self,
        clean_env,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Pin: when ALL three GH_APP_* vars are set, the App-token
        # mint path MUST be tried FIRST -- not bypass to PAT. App
        # tokens have higher rate-limits and per-org scoping.
        from trufflehog_scanner import get_auth_token

        monkeypatch.setenv("GH_APP_ID", "12345")
        monkeypatch.setenv("GH_APP_PRIVATE_KEY", "-----BEGIN PRIVATE KEY-----")
        monkeypatch.setenv("GH_APP_INSTALLATION_ID", "67890")
        monkeypatch.setenv("GH_PAT", "fallback-pat")

        with mock.patch(
            "trufflehog_scanner.get_github_app_token",
            return_value="app-installation-token",
        ) as app_mint:
            tok = get_auth_token()

        assert tok == "app-installation-token"
        app_mint.assert_called_once_with(
            "12345", "-----BEGIN PRIVATE KEY-----", "67890"
        )

    def test_app_failure_falls_back_to_pat(
        self,
        clean_env,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # Pin: if the App-token mint raises, the function logs a
        # WARNING and continues to the PAT path -- NOT re-raise.
        # A bricked GitHub App MUST NOT fail closed against the PAT
        # (else a stale App credential would block all scans).
        caplog.set_level(logging.WARNING)
        monkeypatch.setenv("GH_APP_ID", "12345")
        monkeypatch.setenv("GH_APP_PRIVATE_KEY", "key")
        monkeypatch.setenv("GH_APP_INSTALLATION_ID", "67890")
        monkeypatch.setenv("GH_PAT", "fallback-pat")
        from trufflehog_scanner import get_auth_token

        with mock.patch(
            "trufflehog_scanner.get_github_app_token",
            side_effect=RuntimeError("private key bad"),
        ):
            tok = get_auth_token()

        assert tok == "fallback-pat"
        assert any(
            "GitHub App authentication failed" in r.message
            for r in caplog.records
        )

    def test_gh_token_used_when_no_pat(
        self, clean_env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Pin: GH_TOKEN (Actions runner default) is the third tier --
        # used only if neither App nor PAT is configured.
        monkeypatch.setenv("GH_TOKEN", "actions-token")
        from trufflehog_scanner import get_auth_token

        assert get_auth_token() == "actions-token"

    def test_github_token_alias_used_when_no_gh_token(
        self, clean_env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Pin: GITHUB_TOKEN (legacy alias) is checked AFTER GH_TOKEN.
        # Both env names are GitHub-Actions standard; supporting
        # GITHUB_TOKEN keeps cross-runner portability.
        monkeypatch.setenv("GITHUB_TOKEN", "github-token-legacy")
        from trufflehog_scanner import get_auth_token

        assert get_auth_token() == "github-token-legacy"

    def test_partial_app_creds_skipped_to_next_tier(
        self, clean_env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Pin: a misconfigured App (only 2 of 3 vars set) MUST NOT
        # attempt the mint -- the env-var triple guard prevents
        # NoneType errors deep inside the JWT path.
        monkeypatch.setenv("GH_APP_ID", "12345")
        monkeypatch.setenv("GH_APP_PRIVATE_KEY", "key")
        # GH_APP_INSTALLATION_ID intentionally unset
        monkeypatch.setenv("GH_PAT", "fallback")

        from trufflehog_scanner import get_auth_token

        with mock.patch(
            "trufflehog_scanner.get_github_app_token"
        ) as app_mint:
            tok = get_auth_token()

        assert tok == "fallback"
        app_mint.assert_not_called()


# ---------------------------------------------------------------------------
# parse_args -- CLI surface contract
# ---------------------------------------------------------------------------


class TestParseArgs:
    def _parse(self, argv, monkeypatch: pytest.MonkeyPatch) -> argparse.Namespace:
        # Defaults are os.environ.get(...) at parse-time, so the test
        # setenv MUST already be applied. We do NOT delenv here --
        # callers explicitly set/clear what they need before calling.
        monkeypatch.setattr(sys, "argv", ["trufflehog_scanner.py", *argv])
        import trufflehog_scanner

        return trufflehog_scanner.parse_args()

    def test_default_ndjson_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Pin: the default findings file is ``findings.ndjson`` (the
        # name the trufflehog action writes). Drift breaks the
        # zero-flag invocation.
        ns = self._parse([], monkeypatch)
        assert ns.ndjson == "findings.ndjson"

    def test_default_log_level_from_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Pin: ``--log-level`` defaults to ``$LOG_LEVEL`` env (or
        # "INFO" when unset). Drift would break the K8s ConfigMap
        # that injects LOG_LEVEL=DEBUG for triage.
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        ns = self._parse([], monkeypatch)
        assert ns.log_level == "DEBUG"

    def test_org_default_from_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Pin: --org default is ``$ORG`` env. The CI workflow injects
        # ORG=Anomalous-Ventures; drift to a hardcoded literal would
        # break multi-org scans.
        monkeypatch.setenv("ORG", "test-org")
        ns = self._parse([], monkeypatch)
        assert ns.org == "test-org"

    def test_dry_run_is_store_true(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Pin: --dry-run is store_true (no value). A regression to
        # type=bool would expect ``--dry-run true`` and silently
        # accept any string as truthy.
        ns_off = self._parse([], monkeypatch)
        assert ns_off.dry_run is False
        ns_on = self._parse(["--dry-run"], monkeypatch)
        assert ns_on.dry_run is True

    def test_int_args_typed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Pin: --max-workers and the diag-* counts are typed int.
        # A drift that left them as strings would crash deep in the
        # ThreadPoolExecutor with a TypeError.
        ns = self._parse(
            [
                "--max-workers",
                "8",
                "--diag-max-files",
                "50",
                "--diag-max-lines",
                "5",
                "--summary-detailed-per-repo",
                "20",
                "--summary-detailed-max",
                "500",
            ],
            monkeypatch,
        )
        assert ns.max_workers == 8
        assert ns.diag_max_files == 50
        assert ns.diag_max_lines == 5
        assert ns.summary_detailed_per_repo == 20
        assert ns.summary_detailed_max == 500

    def test_default_title_prefix(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Pin: default title prefix ``[TruffleHog]`` -- this string
        # is what the dedup query greps for to find existing issues.
        # Drift would cause the dedup to miss prior issues and
        # re-create duplicates on every run.
        ns = self._parse([], monkeypatch)
        assert ns.title_prefix == "[TruffleHog]"

    def test_default_diag_max_values(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Pin: diag defaults pre-cap output at 25 files * 3 lines.
        # Drift to 0 would silently disable the diag output; drift
        # to large values would flood operator stdout.
        ns = self._parse([], monkeypatch)
        assert ns.diag_max_files == 25
        assert ns.diag_max_lines == 3

    def test_default_summary_detail_caps(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Pin: summary cap defaults are 10 per-repo + 300 total --
        # the markdown-summary writer relies on these to stay under
        # GitHub's issue body size limit (65536 chars).
        ns = self._parse([], monkeypatch)
        assert ns.summary_detailed_per_repo == 10
        assert ns.summary_detailed_max == 300

    def test_unverified_and_force_create_default_off(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Pin: BOTH ``--include-unverified`` and ``--force-create``
        # are off by default. They are testing escape-hatches that
        # bypass dedup + verified-only filters. Drift to default-on
        # would FLOOD the issue tracker.
        ns = self._parse([], monkeypatch)
        assert ns.include_unverified is False
        assert ns.force_create is False

    def test_target_repo_default_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Pin: ``--target-repo`` defaults to None (not "" string).
        # Production code uses ``if args.target_repo:`` which would
        # treat empty string and None identically -- but downstream
        # split('/') would fail on the empty string.
        ns = self._parse([], monkeypatch)
        assert ns.target_repo is None
