import base64
import http.server
import io
import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from scripts import refresh_live_price


class PricePayloadTest(unittest.TestCase):
    def test_build_payload(self):
        payload = refresh_live_price.build_payload(81.25, 80.0)

        self.assertEqual(payload["price"], 81.25)
        self.assertEqual(payload["prev_close"], 80.0)
        self.assertEqual(payload["change_pct"], 1.56)
        self.assertEqual(payload["source"], "yahoo CL=F")
        self.assertTrue(payload["fetched_at"].endswith("Z"))
        self.assertIsNone(payload["market_time"])

    def test_payload_carries_the_exchange_time_of_the_quote(self):
        payload = refresh_live_price.build_payload(81.25, 80.0, "2026-09-25T20:59:58Z")
        self.assertEqual(payload["market_time"], "2026-09-25T20:59:58Z")

    def test_fetch_quote_reads_regular_market_time(self):
        meta = {"chart": {"result": [{"meta": {
            "regularMarketPrice": 91.4, "previousClose": 92.1, "regularMarketTime": 1790370000}}]}}
        response = mock.MagicMock()
        response.__enter__.return_value = io.BytesIO(json.dumps(meta).encode())
        with mock.patch.object(refresh_live_price.urllib.request, "urlopen", return_value=response):
            price, previous, market_time = refresh_live_price.fetch_quote()
        self.assertEqual((price, previous), (91.4, 92.1))
        self.assertEqual(market_time, "2026-09-25T21:00:00Z")   # Friday 17:00 ET close


class GitHubDestinationTest(unittest.TestCase):
    def test_authenticated_requests_accept_only_the_fixed_repository(self):
        fixed_prefix = (
            "https://api.github.com/repos/"
            "NavnoorBawa/WTI-Crude-Oil-Futures/"
        )
        refresh_live_price._require_github_api_url(
            f"{fixed_prefix}contents/price.json"
        )

        invalid_urls = (
            (
                "http://api.github.com/repos/"
                "NavnoorBawa/WTI-Crude-Oil-Futures/contents/price.json"
            ),
            (
                "https://api.github.com.evil.example/repos/"
                "NavnoorBawa/WTI-Crude-Oil-Futures/contents/price.json"
            ),
            (
                "https://user:secret@api.github.com/repos/"
                "NavnoorBawa/WTI-Crude-Oil-Futures/contents/price.json"
            ),
            (
                "https://api.github.com:444/repos/"
                "NavnoorBawa/WTI-Crude-Oil-Futures/contents/price.json"
            ),
            "https://api.github.com/repos/other/repo/contents/price.json",
            (
                "https://api.github.com/repos/"
                "NavnoorBawa/WTI-Crude-Oil-Futures/contents/price.json#redirect"
            ),
            "/repos/NavnoorBawa/WTI-Crude-Oil-Futures/contents/price.json",
        )
        for url in invalid_urls:
            with self.subTest(url=url), self.assertRaises(ValueError):
                refresh_live_price._require_github_api_url(url)

    def test_url_builder_encodes_path_segments_and_query_values(self):
        url = refresh_live_price._github_repo_url(
            "contents",
            "../../outside?token=stolen#fragment",
            query={"ref": "live-data&admin=true"},
        )

        parsed = urllib.parse.urlsplit(url)
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "api.github.com")
        self.assertEqual(
            parsed.path,
            (
                "/repos/NavnoorBawa/WTI-Crude-Oil-Futures/contents/"
                "..%2F..%2Foutside%3Ftoken%3Dstolen%23fragment"
            ),
        )
        self.assertEqual(
            urllib.parse.parse_qs(parsed.query),
            {"ref": ["live-data&admin=true"]},
        )
        self.assertEqual(parsed.fragment, "")


class PublishPriceTest(unittest.TestCase):
    def test_reloads_sha_after_collision_and_uses_only_fixed_urls(self):
        quote = {"price": 81.25}
        collision = refresh_live_price.ApiError(409, "sha does not match")
        responses = [
            {"object": {"sha": "branch-head"}},
            {"sha": "old-file-sha"},
            collision,
            {"sha": "new-file-sha"},
            {"content": {"sha": "published"}},
        ]

        with (
            mock.patch.object(
                refresh_live_price, "request_json", side_effect=responses
            ) as request,
            mock.patch.object(refresh_live_price.time, "sleep"),
        ):
            refresh_live_price.publish_price(
                quote,
                branch="live-data",
                token="test-token",
            )

        put_payloads = [
            call.kwargs["payload"]
            for call in request.call_args_list
            if call.args[0] == "PUT"
        ]
        self.assertEqual(
            [payload["sha"] for payload in put_payloads],
            ["old-file-sha", "new-file-sha"],
        )

        expected_prefix = "/repos/NavnoorBawa/WTI-Crude-Oil-Futures/"
        for call in request.call_args_list:
            parsed = urllib.parse.urlsplit(call.args[1])
            self.assertEqual(parsed.scheme, "https")
            self.assertEqual(parsed.netloc, "api.github.com")
            self.assertTrue(parsed.path.startswith(expected_prefix))
            self.assertIsNone(parsed.username)
            self.assertIsNone(parsed.password)
            self.assertIsNone(parsed.port)
            self.assertEqual(parsed.fragment, "")

        self.assertEqual(
            urllib.parse.urlsplit(request.call_args_list[0].args[1]).path,
            f"{expected_prefix}git/ref/heads/live-data",
        )
        lookup_calls = [
            call
            for call in request.call_args_list
            if call.args[0] == "GET" and "/contents/" in call.args[1]
        ]
        self.assertEqual(len(lookup_calls), 2)
        for call in lookup_calls:
            self.assertEqual(
                urllib.parse.parse_qs(urllib.parse.urlsplit(call.args[1]).query),
                {"ref": ["live-data"]},
            )

    def test_authenticated_opener_does_not_follow_redirects(self):
        # Drive the REAL opener (not a mock of it) against a local server whose only answer is a
        # redirect: the token-bearing request must stop at the 302 and never reach the target.
        hits = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - stdlib hook name
                hits.append((self.path, self.headers.get("Authorization")))
                if self.path == "/start":
                    self.send_response(302)
                    self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/steal")
                    self.end_headers()
                else:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"{}")

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/start",
            headers={"Authorization": "Bearer secret-token"},
        )
        with self.assertRaises(urllib.error.HTTPError) as raised:
            refresh_live_price._AUTHENTICATED_OPENER.open(request, timeout=5)
        self.assertEqual(raised.exception.code, 302)
        self.assertEqual([path for path, _ in hits], ["/start"])

    def test_request_does_not_retry_authentication_failure(self):
        url = (
            "https://api.github.com/repos/"
            "NavnoorBawa/WTI-Crude-Oil-Futures/contents/price.json"
        )
        response_body = io.BytesIO(
            json.dumps({"message": "Bad credentials"}).encode()
        )
        error = urllib.error.HTTPError(
            url,
            401,
            "Unauthorized",
            {},
            response_body,
        )
        with (
            mock.patch.object(
                refresh_live_price._AUTHENTICATED_OPENER,
                "open",
                side_effect=error,
            ) as opener,
            self.assertRaises(refresh_live_price.ApiError),
        ):
            refresh_live_price.request_json(
                "GET",
                url,
                token="bad-token",
            )

        opener.assert_called_once()


class MainEnvironmentTest(unittest.TestCase):
    def test_main_rejects_unexpected_api_or_repository_before_fetch(self):
        base_environment = {
            "GH_TOKEN": "token",
            "GITHUB_API_URL": "https://api.github.com",
            "GITHUB_REPOSITORY": "NavnoorBawa/WTI-Crude-Oil-Futures",
            "GITHUB_SHA": "main-sha",
        }
        invalid_targets = (
            {"GITHUB_API_URL": "https://api.github.com.evil.example"},
            {"GITHUB_API_URL": "https://user@api.github.com"},
            {"GITHUB_API_URL": "https://api.github.com:444"},
            {"GITHUB_API_URL": "https://api.github.com#fragment"},
            {"GITHUB_REPOSITORY": "attacker/repository"},
        )

        for overrides in invalid_targets:
            environment = base_environment | overrides
            with (
                self.subTest(overrides=overrides),
                mock.patch.dict(os.environ, environment, clear=True),
                mock.patch.object(refresh_live_price, "fetch_quote") as fetch_quote,
                mock.patch.object(refresh_live_price, "publish_price") as publish,
            ):
                self.assertEqual(refresh_live_price.main(), 1)
                fetch_quote.assert_not_called()
                publish.assert_not_called()


class BranchAndStalenessTest(unittest.TestCase):
    def test_missing_state_branch_fails_instead_of_being_recreated(self):
        missing = refresh_live_price.ApiError(404, "Not Found")
        with mock.patch.object(refresh_live_price, "request_json", side_effect=missing) as request:
            with self.assertRaises(refresh_live_price.ApiError) as raised:
                refresh_live_price.ensure_branch(branch="live-data", token="t")
        self.assertIn("missing", str(raised.exception))
        self.assertEqual([c.args[0] for c in request.call_args_list], ["GET"])   # no POST /git/refs

    def _published(self, hours_old):
        fetched = (datetime.now(timezone.utc) - timedelta(hours=hours_old)).isoformat()
        content = base64.b64encode(json.dumps({"price": 90.0, "fetched_at": fetched}).encode())
        return {"content": content.decode()}

    def _main_without_quote(self, hours_old):
        env = {"GH_TOKEN": "t", "GITHUB_API_URL": "https://api.github.com",
               "GITHUB_REPOSITORY": "NavnoorBawa/WTI-Crude-Oil-Futures"}
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch.object(refresh_live_price, "fetch_quote", return_value=None),
            mock.patch.object(refresh_live_price, "request_json", return_value=self._published(hours_old)),
        ):
            return refresh_live_price.main()

    def test_provider_outage_is_a_warning_at_first(self):
        self.assertEqual(self._main_without_quote(3), 0)

    def test_provider_outage_fails_once_the_published_price_is_stale(self):
        self.assertEqual(self._main_without_quote(30), 1)


if __name__ == "__main__":
    unittest.main()
