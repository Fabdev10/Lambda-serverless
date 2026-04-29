import json
import os
import unittest
from base64 import urlsafe_b64encode
from unittest.mock import patch

os.environ.setdefault("ALLOWED_ORIGIN", "https://example.com")
os.environ.setdefault("SNS_TOPIC_ARN", "arn:aws:sns:eu-west-1:123456789012:test-topic")
os.environ.setdefault("SUBMISSIONS_TABLE", "contact-submissions")
os.environ.setdefault("ADMIN_TOKEN", "admin-secret-token")

from src.contact_handler import app


def build_event(body, method="POST", path="/contact"):
    return {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": path,
        "requestContext": {"http": {"method": method}},
        "body": json.dumps(body),
        "isBase64Encoded": False,
    }


class ContactHandlerTests(unittest.TestCase):
    @patch("src.contact_handler.app.get_submissions_table")
    @patch("src.contact_handler.app.get_sns_client")
    def test_returns_success_for_valid_payload(self, get_sns_client_mock, get_submissions_table_mock):
        publish_mock = get_sns_client_mock.return_value.publish
        put_item_mock = get_submissions_table_mock.return_value.put_item
        response = app.lambda_handler(
            build_event(
                {
                    "name": "Fabio Rossi",
                    "email": "fabio@example.com",
                    "message": "Vorrei maggiori informazioni sul vostro servizio.",
                }
            ),
            None,
        )

        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(body["message"], "Message received successfully.")
        publish_mock.assert_called_once()
        put_item_mock.assert_called_once()

    def test_returns_validation_errors(self):
        response = app.lambda_handler(build_event({"name": "", "email": "bad-email", "message": ""}), None)

        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 400)
        self.assertEqual(body["error"], "Validation failed.")
        self.assertEqual(len(body["details"]), 3)

    def test_rejects_invalid_json(self):
        event = build_event({})
        event["body"] = "{not-json}"

        response = app.lambda_handler(event, None)
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 400)
        self.assertEqual(body["error"], "Invalid JSON payload.")

    def test_rejects_non_object_json_payload(self):
        event = build_event({})
        event["body"] = '["not", "an", "object"]'

        response = app.lambda_handler(event, None)
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 400)
        self.assertEqual(body["error"], "Invalid JSON payload.")

    @patch("src.contact_handler.app.get_submissions_table")
    @patch("src.contact_handler.app.get_sns_client")
    def test_honeypot_submission_is_accepted_without_publish(self, get_sns_client_mock, get_submissions_table_mock):
        response = app.lambda_handler(
            build_event(
                {
                    "name": "Fabio Rossi",
                    "email": "fabio@example.com",
                    "message": "Messaggio valido ma honeypot compilato.",
                    "website": "https://spam.example",
                }
            ),
            None,
        )

        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(body["message"], "Message received successfully.")
        get_sns_client_mock.return_value.publish.assert_not_called()
        get_submissions_table_mock.return_value.put_item.assert_not_called()

    @patch("src.contact_handler.app.get_submissions_table")
    @patch("src.contact_handler.app.get_sns_client")
    def test_returns_server_error_when_sns_fails(self, get_sns_client_mock, get_submissions_table_mock):
        get_sns_client_mock.return_value.publish.side_effect = RuntimeError("sns failed")
        response = app.lambda_handler(
            build_event(
                {
                    "name": "Fabio Rossi",
                    "email": "fabio@example.com",
                    "message": "Messaggio di test per controllare gli errori.",
                }
            ),
            None,
        )

        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 500)
        self.assertEqual(body["error"], "Unable to process the request right now.")
        get_submissions_table_mock.return_value.put_item.assert_not_called()

    def test_admin_endpoint_requires_token(self):
        event = build_event({}, method="GET")
        event["headers"] = {}

        response = app.lambda_handler(event, None)
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 401)
        self.assertEqual(body["error"], "Unauthorized.")

    @patch("src.contact_handler.app.get_submissions_table")
    def test_admin_endpoint_returns_submissions(self, get_submissions_table_mock):
        get_submissions_table_mock.return_value.query.return_value = {
            "Items": [
                {
                    "submission_id": "2",
                    "entity_type": "submission",
                    "created_at": "2026-01-02T00:00:00+00:00",
                    "name": "Mario",
                    "email": "mario@example.com",
                    "message": "Messaggio 2",
                },
                {
                    "submission_id": "1",
                    "entity_type": "submission",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "name": "Luigi",
                    "email": "luigi@example.com",
                    "message": "Messaggio 1",
                },
            ],
            "LastEvaluatedKey": {"submission_id": "1", "entity_type": "submission", "created_at": "2026-01-01T00:00:00+00:00"},
        }

        event = build_event({}, method="GET", path="/submissions")
        event["headers"] = {"x-admin-token": "admin-secret-token"}
        event["queryStringParameters"] = {"limit": "1"}

        response = app.lambda_handler(event, None)
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(body["count"], 2)
        self.assertEqual(body["items"][0]["submission_id"], "2")
        self.assertTrue(body["next_cursor"])
        get_submissions_table_mock.return_value.query.assert_called_once()

    @patch("src.contact_handler.app.get_submissions_table")
    def test_admin_endpoint_supports_cursor(self, get_submissions_table_mock):
        cursor = urlsafe_b64encode(
            json.dumps({"submission_id": "2", "entity_type": "submission", "created_at": "2026-01-02T00:00:00+00:00"}).encode("utf-8")
        ).decode("utf-8")
        get_submissions_table_mock.return_value.query.return_value = {"Items": []}

        event = build_event({}, method="GET", path="/submissions")
        event["headers"] = {"x-admin-token": "admin-secret-token"}
        event["queryStringParameters"] = {"limit": "10", "cursor": cursor}

        response = app.lambda_handler(event, None)

        self.assertEqual(response["statusCode"], 200)
        get_submissions_table_mock.return_value.query.assert_called_once()

    def test_admin_endpoint_rejects_invalid_cursor(self):
        event = build_event({}, method="GET", path="/submissions")
        event["headers"] = {"x-admin-token": "admin-secret-token"}
        event["queryStringParameters"] = {"cursor": "%%%invalid%%%"}

        response = app.lambda_handler(event, None)
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 400)
        self.assertEqual(body["error"], "Invalid pagination cursor.")

    def test_stats_endpoint_requires_token(self):
        event = build_event({}, method="GET", path="/submissions/stats")
        event["headers"] = {}

        response = app.lambda_handler(event, None)
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 401)
        self.assertEqual(body["error"], "Unauthorized.")

    @patch("src.contact_handler.app.count_submissions")
    def test_stats_endpoint_returns_aggregated_totals(self, count_submissions_mock):
        count_submissions_mock.side_effect = [42, 5, 12, 20]
        event = build_event({}, method="GET", path="/submissions/stats")
        event["headers"] = {"x-admin-token": "admin-secret-token"}

        response = app.lambda_handler(event, None)
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(body["totals"]["all_time"], 42)
        self.assertEqual(body["totals"]["last_24_hours"], 5)
        self.assertEqual(body["totals"]["last_7_days"], 12)
        self.assertEqual(body["totals"]["last_30_days"], 20)
        self.assertTrue(body["generated_at"])
        self.assertEqual(count_submissions_mock.call_count, 4)

    def test_health_check_returns_200(self):
        event = build_event({}, method="GET", path="/health")

        response = app.lambda_handler(event, None)
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(body["status"], "ok")

    def test_unknown_path_returns_404(self):
        event = build_event({}, method="GET", path="/unknown")

        response = app.lambda_handler(event, None)
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 404)

    def test_admin_endpoint_requires_token(self):
        event = build_event({}, method="GET", path="/submissions")
        event["headers"] = {}

        response = app.lambda_handler(event, None)
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 401)
        self.assertEqual(body["error"], "Unauthorized.")

    def test_delete_submission_requires_token(self):
        event = build_event({}, method="DELETE", path="/submissions/abc-123")
        event["headers"] = {}

        response = app.lambda_handler(event, None)
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 401)
        self.assertEqual(body["error"], "Unauthorized.")

    @patch("src.contact_handler.app.get_submissions_table")
    def test_delete_submission_removes_item(self, get_submissions_table_mock):
        event = build_event({}, method="DELETE", path="/submissions/abc-123")
        event["headers"] = {"x-admin-token": "admin-secret-token"}

        response = app.lambda_handler(event, None)
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(body["message"], "Submission deleted.")
        get_submissions_table_mock.return_value.delete_item.assert_called_once_with(
            Key={"submission_id": "abc-123"}
        )

    @patch("src.contact_handler.app.get_rate_limits_table")
    @patch("src.contact_handler.app.get_submissions_table")
    @patch("src.contact_handler.app.get_sns_client")
    def test_rate_limit_allows_normal_requests(
        self, get_sns_client_mock, get_submissions_table_mock, get_rate_limits_table_mock
    ):
        get_rate_limits_table_mock.return_value.update_item.return_value = {
            "Attributes": {"count": 1}
        }
        event = build_event(
            {"name": "Fabio", "email": "fabio@example.com", "message": "Ciao."},
            method="POST",
        )
        event["requestContext"]["http"]["sourceIp"] = "203.0.113.1"

        response = app.lambda_handler(event, None)

        self.assertEqual(response["statusCode"], 200)

    @patch("src.contact_handler.app.get_rate_limits_table")
    def test_rate_limit_blocks_excess_requests(self, get_rate_limits_table_mock):
        get_rate_limits_table_mock.return_value.update_item.return_value = {
            "Attributes": {"count": 11}
        }
        event = build_event(
            {"name": "Fabio", "email": "fabio@example.com", "message": "Ciao."},
            method="POST",
        )
        event["requestContext"]["http"]["sourceIp"] = "203.0.113.1"

        response = app.lambda_handler(event, None)
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 429)
        self.assertIn("Too many requests", body["error"])


if __name__ == "__main__":
    unittest.main()
