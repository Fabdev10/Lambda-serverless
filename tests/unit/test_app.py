import json
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("ALLOWED_ORIGIN", "https://example.com")
os.environ.setdefault("SNS_TOPIC_ARN", "arn:aws:sns:eu-west-1:123456789012:test-topic")

from src.contact_handler import app


def build_event(body, method="POST"):
    return {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": "/contact",
        "requestContext": {"http": {"method": method}},
        "body": json.dumps(body),
        "isBase64Encoded": False,
    }


class ContactHandlerTests(unittest.TestCase):
    @patch("src.contact_handler.app.get_sns_client")
    def test_returns_success_for_valid_payload(self, get_sns_client_mock):
        publish_mock = get_sns_client_mock.return_value.publish
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

    @patch("src.contact_handler.app.get_sns_client")
    def test_honeypot_submission_is_accepted_without_publish(self, get_sns_client_mock):
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

    @patch("src.contact_handler.app.get_sns_client")
    def test_returns_server_error_when_sns_fails(self, get_sns_client_mock):
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


if __name__ == "__main__":
    unittest.main()
