import json
import os
import re
import time
import uuid
from base64 import urlsafe_b64decode, urlsafe_b64encode
from datetime import datetime, timedelta, timezone

try:
    import boto3
    from boto3.dynamodb.conditions import Key as DynamoKey
except ModuleNotFoundError:
    boto3 = None
    DynamoKey = None


EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
MAX_NAME_LENGTH = 100
MAX_MESSAGE_LENGTH = 2000
HONEYPOT_FIELD = "website"
ADMIN_TOKEN_HEADER = "x-admin-token"
DEFAULT_LIST_LIMIT = 20
MAX_LIST_LIMIT = 100
RATELIMIT_MAX_PER_MINUTE = 10
SUBMISSIONS_GSI_NAME = "ByEntityTypeCreatedAt"
SUBMISSIONS_ENTITY_TYPE = "submission"


def get_sns_client():
    if boto3 is None:
        raise RuntimeError("boto3 is required to publish SNS notifications")

    return boto3.client("sns")


def get_dynamodb_resource():
    if boto3 is None:
        raise RuntimeError("boto3 is required to access DynamoDB")

    return boto3.resource("dynamodb")


def get_submissions_table():
    table_name = os.environ.get("SUBMISSIONS_TABLE")
    if not table_name:
        raise RuntimeError("SUBMISSIONS_TABLE environment variable is not configured")

    return get_dynamodb_resource().Table(table_name)


def get_rate_limits_table():
    table_name = os.environ.get("RATE_LIMITS_TABLE")
    if not table_name:
        return None

    return get_dynamodb_resource().Table(table_name)


def build_response(status_code, payload):
    return {
        "statusCode": status_code,
        "headers": {
            "Access-Control-Allow-Origin": os.environ.get("ALLOWED_ORIGIN", "*"),
            "Access-Control-Allow-Headers": "content-type,x-admin-token",
            "Access-Control-Allow-Methods": "GET,POST,DELETE,OPTIONS",
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


def get_admin_token_from_request(event):
    headers = event.get("headers") or {}
    if not isinstance(headers, dict):
        return ""

    for key, value in headers.items():
        if key and key.lower() == ADMIN_TOKEN_HEADER:
            return (value or "").strip()

    return ""


def parse_limit(event):
    query_params = event.get("queryStringParameters") or {}
    if not isinstance(query_params, dict):
        return DEFAULT_LIST_LIMIT

    limit_raw = query_params.get("limit")
    if not limit_raw:
        return DEFAULT_LIST_LIMIT

    try:
        limit_value = int(limit_raw)
    except (TypeError, ValueError):
        return DEFAULT_LIST_LIMIT

    if limit_value < 1:
        return 1
    if limit_value > MAX_LIST_LIMIT:
        return MAX_LIST_LIMIT

    return limit_value


def parse_cursor(event):
    query_params = event.get("queryStringParameters") or {}
    if not isinstance(query_params, dict):
        return None

    cursor = (query_params.get("cursor") or "").strip()
    if not cursor:
        return None

    try:
        decoded = urlsafe_b64decode(cursor.encode("utf-8")).decode("utf-8")
        last_key = json.loads(decoded)
    except Exception as error:
        raise ValueError("Invalid pagination cursor.") from error

    if not isinstance(last_key, dict):
        raise ValueError("Invalid pagination cursor.")

    return last_key


def encode_cursor(last_evaluated_key):
    if not last_evaluated_key:
        return ""

    return urlsafe_b64encode(json.dumps(last_evaluated_key).encode("utf-8")).decode("utf-8")


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


def save_submission(payload, event):
    request_context = event.get("requestContext") or {}
    http_context = request_context.get("http") or {}
    source_ip = http_context.get("sourceIp", "")
    user_agent = http_context.get("userAgent", "")
    created_at = datetime.now(timezone.utc).isoformat()

    item = {
        "submission_id": str(uuid.uuid4()),
        "entity_type": SUBMISSIONS_ENTITY_TYPE,
        "created_at": created_at,
        "name": payload["name"],
        "email": payload["email"],
        "message": payload["message"],
        "source_ip": source_ip,
        "user_agent": user_agent,
    }

    get_submissions_table().put_item(Item=item)


def list_submissions(limit, cursor):
    query_kwargs = {
        "IndexName": SUBMISSIONS_GSI_NAME,
        "KeyConditionExpression": DynamoKey("entity_type").eq(SUBMISSIONS_ENTITY_TYPE),
        "ScanIndexForward": False,
        "Limit": limit,
    }
    if cursor:
        query_kwargs["ExclusiveStartKey"] = cursor

    response = get_submissions_table().query(**query_kwargs)
    items = response.get("Items", [])
    next_cursor = encode_cursor(response.get("LastEvaluatedKey"))

    return {"items": items, "next_cursor": next_cursor}


def count_submissions(created_at_start=None, created_at_end=None):
    key_condition = DynamoKey("entity_type").eq(SUBMISSIONS_ENTITY_TYPE)

    if created_at_start and created_at_end:
        key_condition = key_condition & DynamoKey("created_at").between(
            created_at_start, created_at_end
        )
    elif created_at_start:
        key_condition = key_condition & DynamoKey("created_at").gte(created_at_start)
    elif created_at_end:
        key_condition = key_condition & DynamoKey("created_at").lte(created_at_end)

    total = 0
    exclusive_start_key = None

    while True:
        query_kwargs = {
            "IndexName": SUBMISSIONS_GSI_NAME,
            "KeyConditionExpression": key_condition,
            "Select": "COUNT",
        }
        if exclusive_start_key:
            query_kwargs["ExclusiveStartKey"] = exclusive_start_key

        response = get_submissions_table().query(**query_kwargs)
        total += int(response.get("Count", 0))
        exclusive_start_key = response.get("LastEvaluatedKey")
        if not exclusive_start_key:
            break

    return total


def delete_submission(submission_id):
    get_submissions_table().delete_item(Key={"submission_id": submission_id})


def check_rate_limit(source_ip):
    if not source_ip:
        return True

    table = get_rate_limits_table()
    if table is None:
        return True

    window = str(int(time.time()) // 60)
    key = f"{source_ip}#{window}"
    expires_at = int(time.time()) + 120

    try:
        response = table.update_item(
            Key={"ip_minute": key},
            UpdateExpression="ADD #cnt :one SET expires_at = :exp",
            ExpressionAttributeNames={"#cnt": "count"},
            ExpressionAttributeValues={":one": 1, ":exp": expires_at},
            ReturnValues="UPDATED_NEW",
        )
        count = int(response["Attributes"].get("count", 1))
        return count <= RATELIMIT_MAX_PER_MINUTE
    except Exception:
        return True


def handle_health():
    return build_response(
        200,
        {"status": "ok", "service": os.environ.get("POWERTOOLS_SERVICE_NAME", "contact-webapp")},
    )


def handle_list_submissions(event):
    expected_token = os.environ.get("ADMIN_TOKEN", "")
    request_token = get_admin_token_from_request(event)
    if not expected_token or request_token != expected_token:
        return build_response(401, {"error": "Unauthorized."})

    try:
        limit = parse_limit(event)
        cursor = parse_cursor(event)
    except ValueError as error:
        return build_response(400, {"error": str(error)})

    try:
        result = list_submissions(limit, cursor)
    except Exception:
        return build_response(500, {"error": "Unable to list submissions right now."})

    submissions = result["items"]
    return build_response(
        200,
        {
            "count": len(submissions),
            "items": submissions,
            "next_cursor": result["next_cursor"],
        },
    )


def handle_delete_submission(event, submission_id):
    expected_token = os.environ.get("ADMIN_TOKEN", "")
    request_token = get_admin_token_from_request(event)
    if not expected_token or request_token != expected_token:
        return build_response(401, {"error": "Unauthorized."})

    if not submission_id:
        return build_response(400, {"error": "Submission ID is required."})

    try:
        delete_submission(submission_id)
    except Exception:
        return build_response(500, {"error": "Unable to delete submission right now."})

    return build_response(200, {"message": "Submission deleted."})


def handle_submission_stats(event):
    expected_token = os.environ.get("ADMIN_TOKEN", "")
    request_token = get_admin_token_from_request(event)
    if not expected_token or request_token != expected_token:
        return build_response(401, {"error": "Unauthorized."})

    now_utc = datetime.now(timezone.utc)
    now_iso = now_utc.isoformat()
    window_24h = (now_utc - timedelta(hours=24)).isoformat()
    window_7d = (now_utc - timedelta(days=7)).isoformat()
    window_30d = (now_utc - timedelta(days=30)).isoformat()

    try:
        total = count_submissions()
        last_24_hours = count_submissions(created_at_start=window_24h, created_at_end=now_iso)
        last_7_days = count_submissions(created_at_start=window_7d, created_at_end=now_iso)
        last_30_days = count_submissions(created_at_start=window_30d, created_at_end=now_iso)
    except Exception:
        return build_response(500, {"error": "Unable to load submission stats right now."})

    return build_response(
        200,
        {
            "generated_at": now_iso,
            "totals": {
                "all_time": total,
                "last_24_hours": last_24_hours,
                "last_7_days": last_7_days,
                "last_30_days": last_30_days,
            },
        },
    )


def handle_contact(event):
    request_context = event.get("requestContext") or {}
    http_context = request_context.get("http") or {}
    source_ip = http_context.get("sourceIp", "")

    if not check_rate_limit(source_ip):
        return build_response(429, {"error": "Too many requests. Please try again later."})

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
        save_submission(payload, event)
    except Exception:
        return build_response(500, {"error": "Unable to process the request right now."})

    return build_response(200, {"message": "Message received successfully."})


def lambda_handler(event, _context):
    method = (
        (event.get("requestContext") or {}).get("http", {}).get("method")
        or event.get("httpMethod")
        or ""
    ).upper()
    path = event.get("rawPath", "")

    if method == "OPTIONS":
        return build_response(200, {"message": "ok"})

    if method == "GET" and path == "/health":
        return handle_health()

    if method == "GET" and path == "/submissions":
        return handle_list_submissions(event)

    if method == "GET" and path == "/submissions/stats":
        return handle_submission_stats(event)

    if method == "DELETE" and path.startswith("/submissions/"):
        submission_id = path.split("/submissions/", 1)[1].strip("/")
        return handle_delete_submission(event, submission_id)

    if method == "POST":
        return handle_contact(event)

    return build_response(404, {"error": "Not found."})
