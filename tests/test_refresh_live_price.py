import io
import json
import urllib.error
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


class PublishPriceTest(unittest.TestCase):
    def test_reloads_sha_after_collision(self):
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
                api_url="https://api.github.test",
                repository="owner/repo",
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

    def test_request_does_not_retry_authentication_failure(self):
        response_body = io.BytesIO(json.dumps({"message": "Bad credentials"}).encode())
        error = urllib.error.HTTPError(
            "https://api.github.test",
            401,
            "Unauthorized",
            {},
            response_body,
        )
        with (
            mock.patch.object(
                refresh_live_price.urllib.request,
                "urlopen",
                side_effect=error,
            ) as urlopen,
            self.assertRaises(refresh_live_price.ApiError),
        ):
            refresh_live_price.request_json(
                "GET",
                "https://api.github.test",
                token="bad-token",
            )

        urlopen.assert_called_once()


if __name__ == "__main__":
    unittest.main()
