import io
import json
import os
import urllib.error
import urllib.parse
import unittest
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
                start_sha="main-sha",
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

    def test_request_does_not_follow_redirects_with_token(self):
        response_body = io.BytesIO(
            json.dumps({"message": "Found"}).encode("utf-8")
        )
        error = urllib.error.HTTPError(
            (
                "https://api.github.com/repos/"
                "NavnoorBawa/WTI-Crude-Oil-Futures/contents/price.json"
            ),
            302,
            "Found",
            {"Location": "https://attacker.example/steal"},
            response_body,
        )
        with (
            mock.patch.object(
                refresh_live_price._AUTHENTICATED_OPENER,
                "open",
                side_effect=error,
            ) as opener,
            self.assertRaises(refresh_live_price.ApiError) as raised,
        ):
            refresh_live_price.request_json(
                "GET",
                (
                    "https://api.github.com/repos/"
                    "NavnoorBawa/WTI-Crude-Oil-Futures/contents/price.json"
                ),
                token="secret-token",
            )

        self.assertEqual(raised.exception.status, 302)
        opener.assert_called_once()
        sent_request = opener.call_args.args[0]
        self.assertEqual(sent_request.host, "api.github.com")
        self.assertEqual(
            sent_request.get_header("Authorization"),
            "Bearer secret-token",
        )

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


if __name__ == "__main__":
    unittest.main()
