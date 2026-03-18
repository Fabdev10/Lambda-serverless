import json
import os
import re
from datetime import datetime, timezone

try:
    import boto3
except ModuleNotFoundError:
    boto3 = None


EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
MAX_NAME_LENGTH = 100
MAX_MESSAGE_LENGTH = 2000
HONEYPOT_FIELD = "website"


def get_sns_client():
    if boto3 is None:
        raise RuntimeError("boto3 is required to publish SNS notifications")

    return boto3.client("sns")


def build_response(status_code, payload):
    return {
        "statusCode": status_code,
        "headers": {
            "Access-Control-Allow-Origin": os.environ.get("ALLOWED_ORIGIN", "*"),
            "Access-Control-Allow-Headers": "content-type",
            "Access-Control-Allow-Methods": "POST,OPTIONS",
            "Content-Type": "application/json",
        },
        "body": json.dumps(payload),
    }


def parse_event_body(event):
    body = event.get("body")
    if not body:
        return {}

    if event.get("isBase64Encoded"):
        raise ValueError("Base64 encoded payloads are not supported")

    if isinstance(body, str):
        payload = json.loads(body)
        if isinstance(payload, dict):
            return payload
        raise ValueError("Unsupported request body")

    if isinstance(body, dict):
        return body

    raise ValueError("Unsupported request body")


def is_honeypot_triggered(payload):
    honeypot_value = payload.get(HONEYPOT_FIELD)
    if honeypot_value is None:
        return False

    if isinstance(honeypot_value, str):
        return bool(honeypot_value.strip())

    return True


def validate_payload(payload):
    errors = []

    name = (payload.get("name") or "").strip()
    email = (payload.get("email") or "").strip()
    message = (payload.get("message") or "").strip()

    if not name:
        errors.append({"field": "name", "message": "Name is required."})
    elif len(name) > MAX_NAME_LENGTH:
        errors.append(
            {"field": "name", "message": f"Name must be <= {MAX_NAME_LENGTH} characters."}
        )

    if not email:
        errors.append({"field": "email", "message": "Email is required."})
    elif not EMAIL_PATTERN.match(email):
        errors.append({"field": "email", "message": "Email format is invalid."})

    if not message:
        errors.append({"field": "message", "message": "Message is required."})
    elif len(message) > MAX_MESSAGE_LENGTH:
        errors.append(
            {
                "field": "message",
                "message": f"Message must be <= {MAX_MESSAGE_LENGTH} characters.",
            }
        )

    return errors


def build_notification(payload):
    timestamp = datetime.now(timezone.utc).isoformat()
    return {
        "Subject": f"New contact form submission from {payload['name']}",
        "Message": "\n".join(
            [
                "New contact request received.",
                "",
                f"Name: {payload['name']}",
                f"Email: {payload['email']}",
                f"Message: {payload['message']}",
                f"ReceivedAt: {timestamp}",
            ]
        ),
    }


def publish_notification(payload):
    notification = build_notification(payload)
    get_sns_client().publish(
        TopicArn=os.environ["SNS_TOPIC_ARN"],
        Subject=notification["Subject"],
        Message=notification["Message"],
    )


def lambda_handler(event, _context):
    method = event.get("requestContext", {}).get("http", {}).get("method") or event.get("httpMethod")
    if method == "OPTIONS":
        return build_response(200, {"message": "ok"})

    try:
        payload = parse_event_body(event)
    except (ValueError, json.JSONDecodeError):
        return build_response(400, {"error": "Invalid JSON payload."})

    # Return success without publishing to reduce bot feedback loops.
    if is_honeypot_triggered(payload):
        return build_response(200, {"message": "Message received successfully."})

    errors = validate_payload(payload)
    if errors:
        return build_response(400, {"error": "Validation failed.", "details": errors})

    try:
        publish_notification(payload)
    except Exception:
        return build_response(500, {"error": "Unable to process the request right now."})

    return build_response(200, {"message": "Message received successfully."})
