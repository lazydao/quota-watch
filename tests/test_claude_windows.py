import io
import json
import tempfile
import unittest
import urllib.error
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from quota_watch.claude import (
    ClaudeError,
    _NoUsageRedirect,
    _write_snapshot,
    fetch_claude_snapshot,
    parse_oauth_usage,
    query_oauth_usage_windows,
)
from quota_watch.models import ProviderSnapshot, QuotaBucket, QuotaWindow, unavailable_snapshot


def usage(percent=12):
    return {
        "five_hour": {"utilization": percent, "resets_at": "2033-05-18T03:33:20Z"},
        "subscription_type": "max",
    }


class WindowsUsageTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.config_dir = Path(directory.name)
        self.credentials = self.config_dir / ".credentials.json"
        self.oauth = {
            "accessToken": "private-file-token",
            "refreshToken": "private-refresh-token",
            "expiresAt": 2_000_000_000_000,
            "scopes": ["user:profile", "user:inference"],
            "subscriptionType": "max",
        }
        self.write_credentials()
        self.environment = SimpleNamespace(name="nt", environ={"CLAUDE_CONFIG_DIR": str(self.config_dir)})
        environment = patch("quota_watch.claude.os", self.environment)
        self.addCleanup(environment.stop)
        environment.start()
        self.opener = Mock()
        self.set_response(usage())
        opener = patch("quota_watch.claude.urllib.request.build_opener", return_value=self.opener)
        self.addCleanup(opener.stop)
        self.build_opener = opener.start()
        processes = patch("quota_watch.claude.subprocess.run")
        self.addCleanup(processes.stop)
        self.processes = processes.start()

    def write_credentials(self):
        self.credentials.write_text(json.dumps({"claudeAiOauth": self.oauth}), encoding="utf-8-sig")

    def set_response(self, payload):
        self.opener.open.return_value = io.BytesIO(json.dumps(payload).encode("utf-8"))

    def test_native_query_is_get_filtered_and_does_not_change_credentials(self):
        before = self.credentials.read_bytes()
        payload = usage()
        payload["account"] = "private-account"
        payload["five_hour"]["extra"] = "private-field"
        payload["limits"] = [{
            "kind": "weekly_scoped", "percent": 15,
            "scope": {"model": {"display_name": "Fable"}, "org": "private-org"},
        }]
        self.set_response(payload)
        result = query_oauth_usage_windows(timeout=4)
        request = self.opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.anthropic.com/api/oauth/usage")
        self.assertEqual(request.get_method(), "GET")
        self.assertIsNone(request.data)
        self.assertEqual(request.get_header("Authorization"), "Bearer private-file-token")
        self.assertEqual(self.opener.open.call_args.kwargs["timeout"], 4)
        self.assertIsInstance(self.build_opener.call_args.args[0], _NoUsageRedirect)
        self.assertEqual(result["subscription_type"], "max")
        self.assertEqual(result["limits"][0]["model_name"], "Fable")
        self.assertNotIn("private", json.dumps(result))
        self.assertEqual(self.credentials.read_bytes(), before)
        self.processes.assert_not_called()

    def test_environment_token_has_priority_without_reading_file_or_merging_plan(self):
        self.environment.environ["CLAUDE_CODE_OAUTH_TOKEN"] = "  private-env-token  "
        with patch.object(Path, "read_text", side_effect=AssertionError("credential file must not be read")):
            result = query_oauth_usage_windows()
        request = self.opener.open.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer private-env-token")
        self.assertIsNone(result["subscription_type"])

    def test_known_expired_token_fails_without_network_or_refresh(self):
        self.oauth["expiresAt"] = 1
        self.write_credentials()
        before = self.credentials.read_bytes()
        with self.assertRaisesRegex(ClaudeError, "expired"):
            query_oauth_usage_windows()
        self.opener.open.assert_not_called()
        self.processes.assert_not_called()
        self.assertEqual(self.credentials.read_bytes(), before)

    def test_known_inference_only_scope_fails_without_network(self):
        self.oauth["scopes"] = ["user:inference"]
        self.write_credentials()
        with self.assertRaisesRegex(ClaudeError, "user:profile"):
            query_oauth_usage_windows()
        self.opener.open.assert_not_called()

    def test_missing_and_invalid_credentials_are_safe(self):
        for payload in (None, [], {}, {"claudeAiOauth": []}, {"claudeAiOauth": {"accessToken": True}}):
            with self.subTest(payload=payload):
                self.credentials.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaises(ClaudeError):
                    query_oauth_usage_windows()
        self.credentials.write_bytes(b"\xff")
        with self.assertRaises(ClaudeError):
            query_oauth_usage_windows()
        self.credentials.unlink()
        with self.assertRaises(ClaudeError):
            query_oauth_usage_windows()
        self.opener.open.assert_not_called()

    def test_oversized_or_unreadable_credentials_fail_before_network(self):
        with patch.object(Path, "read_text", side_effect=PermissionError("private-file-token")):
            with self.assertRaises(ClaudeError) as caught:
                query_oauth_usage_windows()
            self.assertNotIn("private", str(caught.exception))
        with patch.object(Path, "stat", return_value=SimpleNamespace(st_size=2 * 1024 * 1024)):
            with self.assertRaises(ClaudeError):
                query_oauth_usage_windows()
        self.opener.open.assert_not_called()

    def test_http_errors_are_sanitized_and_do_not_refresh(self):
        for code in (302, 401, 403, 429, 500):
            with self.subTest(code=code):
                self.opener.open.side_effect = urllib.error.HTTPError(
                    "https://api.anthropic.com/api/oauth/usage", code, "private-file-token", {},
                    io.BytesIO(b"private-server-body"),
                )
                with self.assertRaisesRegex(ClaudeError, str(code)) as caught:
                    query_oauth_usage_windows()
                self.assertNotIn("private", str(caught.exception))
        self.processes.assert_not_called()

    def test_network_errors_never_expose_exception_text(self):
        for error in (urllib.error.URLError("private-file-token"), TimeoutError("private-file-token"), ValueError("private-file-token")):
            with self.subTest(error=type(error)):
                self.opener.open.side_effect = error
                with self.assertRaises(ClaudeError) as caught:
                    query_oauth_usage_windows()
                self.assertNotIn("private", str(caught.exception))

    def test_malformed_or_oversized_responses_fail_safely(self):
        for body in (b"{", b"\xff", b"null", b"[]", b"x" * (1024 * 1024 + 1)):
            with self.subTest(size=len(body)):
                self.opener.open.return_value = io.BytesIO(body)
                with self.assertRaises(ClaudeError):
                    query_oauth_usage_windows()

    def test_redirect_handler_refuses_to_forward_credentials(self):
        self.assertIsNone(_NoUsageRedirect().redirect_request(None, None, 302, "redirect", {}, "https://example.com"))

    def test_non_windows_and_zero_timeout_do_not_query(self):
        self.environment.name = "posix"
        with self.assertRaises(ClaudeError):
            query_oauth_usage_windows()
        self.environment.name = "nt"
        with self.assertRaises(ClaudeError):
            query_oauth_usage_windows(timeout=0)
        self.opener.open.assert_not_called()


class WindowsFirstCollectionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.cache = Path(directory.name) / "claude.json"
        self.state = Path(directory.name) / "refresh.json"
        for attribute, target, kwargs in (
            ("native", "quota_watch.claude.query_oauth_usage_windows", {"return_value": usage(12)}),
            ("wsl", "quota_watch.claude.query_oauth_usage_wsl", {"return_value": usage(30)}),
            ("desktop", "quota_watch.claude.read_desktop_snapshot", {"return_value": unavailable_snapshot("claude", "no desktop")}),
            ("plan", "quota_watch.claude.query_subscription_type", {"return_value": None}),
        ):
            patcher = patch(target, **kwargs)
            self.addCleanup(patcher.stop)
            setattr(self, attribute, patcher.start())

    def fetch(self):
        return fetch_claude_snapshot(destination=self.cache, refresh_state=self.state)

    def write_cache(self, age_minutes=0, plan_type="max"):
        snapshot = parse_oauth_usage(usage(20))
        snapshot = replace(
            snapshot,
            captured_at=(datetime.now(timezone.utc) - timedelta(minutes=age_minutes)).isoformat(),
            buckets=[replace(bucket, plan_type=plan_type) for bucket in snapshot.buckets],
        )
        _write_snapshot(self.cache, snapshot)
        return self.cache.read_bytes()

    def test_windows_query_wins_and_never_starts_wsl_even_with_missing_cached_plan(self):
        self.write_cache(plan_type=None)
        result = self.fetch()
        self.assertEqual(result.buckets[0].windows[0].used_percent, 12)
        self.native.assert_called_once()
        self.wsl.assert_not_called()
        self.desktop.assert_not_called()
        self.plan.assert_not_called()

    def test_cooldown_does_not_repeat_any_active_query(self):
        self.fetch()
        result = self.fetch()
        self.assertEqual(result.buckets[0].windows[0].used_percent, 12)
        self.native.assert_called_once()
        self.wsl.assert_not_called()

    def test_native_failure_prefers_fresh_local_cache_without_wsl_or_auth_status(self):
        before = self.write_cache(plan_type=None)
        self.native.side_effect = ClaudeError("expired")
        result = self.fetch()
        self.assertEqual(result.buckets[0].windows[0].used_percent, 20)
        self.assertEqual(self.cache.read_bytes(), before)
        self.wsl.assert_not_called()
        self.desktop.assert_not_called()
        self.plan.assert_not_called()

    def test_fresh_windows_desktop_history_prevents_wsl_and_preserves_partial_data(self):
        before = self.write_cache(age_minutes=30)
        self.native.side_effect = ClaudeError("expired")
        self.desktop.return_value = ProviderSnapshot(
            provider="claude", status="available", message="Claude Desktop fallback",
            buckets=[QuotaBucket(bucket_id="claude", name="All models", windows=[QuotaWindow("5h", 9, 300)])],
        )
        result = self.fetch()
        self.assertEqual(result.buckets[0].windows[0].used_percent, 9)
        self.assertIsNone(result.buckets[0].windows[0].resets_at)
        self.assertEqual(self.cache.read_bytes(), before)
        self.wsl.assert_not_called()

    def test_wsl_is_used_only_after_windows_has_no_fresh_usable_data(self):
        self.write_cache(age_minutes=30)
        self.native.side_effect = ClaudeError("expired")
        result = self.fetch()
        self.assertEqual(result.buckets[0].windows[0].used_percent, 30)
        self.wsl.assert_called_once()
        self.assertGreater(self.wsl.call_args.kwargs["timeout"], 0)
        self.assertLessEqual(self.wsl.call_args.kwargs["timeout"], 8)

    def test_windows_response_without_usable_windows_uses_fallback(self):
        self.native.return_value = {"five_hour": None, "limits": [{"kind": "session"}]}
        result = self.fetch()
        self.assertEqual(result.buckets[0].windows[0].used_percent, 30)
        self.wsl.assert_called_once()

    def test_total_failure_keeps_old_capture_and_cache(self):
        before = self.write_cache(age_minutes=30)
        self.native.side_effect = ClaudeError("expired")
        self.wsl.side_effect = ClaudeError("offline")
        result = self.fetch()
        self.assertEqual(result.status, "stale")
        self.assertEqual(self.cache.read_bytes(), before)
        self.assertEqual(result.captured_at, json.loads(before)["captured_at"])

    def test_missing_sources_return_unavailable_instead_of_throwing(self):
        self.native.side_effect = ClaudeError("expired")
        self.wsl.side_effect = ClaudeError("offline")
        result = self.fetch()
        self.assertEqual(result.status, "unavailable")
        self.assertIn("Windows:", result.message)
        self.assertIn("WSL fallback:", result.message)
        self.assertFalse(self.cache.exists())

    def test_native_timeout_does_not_add_another_full_timeout_for_wsl(self):
        self.native.side_effect = ClaudeError("timeout")
        with patch("quota_watch.claude.time.monotonic", side_effect=[100, 109]):
            result = self.fetch()
        self.assertEqual(result.status, "unavailable")
        self.wsl.assert_not_called()


if __name__ == "__main__":
    unittest.main()
