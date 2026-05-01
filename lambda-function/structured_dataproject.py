import json
import re
import boto3
from decimal import Decimal
from boto3.dynamodb.conditions import Key

bedrock = boto3.client("bedrock-runtime")
dynamodb = boto3.resource("dynamodb")

MODEL_ID = "amazon.nova-micro-v1:0"
TABLE_NAME = "structured_dataproject"
table = dynamodb.Table(TABLE_NAME)

SYSTEM_PROMPT = """
Convert the input into valid JSON only.

Rules:
- Return ONLY JSON
- No markdown
- No explanations
- Response must start with { and end with }
"""


def lambda_handler(event, context):
    try:
        path = event.get("rawPath") or event.get("path", "")
        method = (
            event.get("requestContext", {})
            .get("http", {})
            .get("method")
            or event.get("httpMethod", "")
        )

        if method == "OPTIONS":
            return response(200, {"message": "OK"})

        user_id = get_user_id(event)

        if not user_id:
            return response(401, {"error": "Unauthorized"})

        if path.endswith("/generate") and method == "POST":
            return generate_handler(event, context, user_id)

        if path.endswith("/save") and method == "POST":
            return save_handler(event, context, user_id)

        if path.endswith("/saved") and method == "GET":
            return saved_handler(user_id)

        return response(404, {"error": "Route not found"})

    except Exception as e:
        return response(500, {"error": str(e)})


def generate_handler(event, context, user_id):
    body = parse_body(event)
    user_input = body.get("prompt", "").strip()

    if not user_input:
        return response(400, {"error": "Prompt is required"})

    r = bedrock.converse(
        modelId=MODEL_ID,
        system=[{"text": SYSTEM_PROMPT}],
        messages=[
            {
                "role": "user",
                "content": [{"text": user_input}]
            }
        ],
        inferenceConfig={"maxTokens": 300, "temperature": 0}
    )

    text = r["output"]["message"]["content"][0]["text"]
    item = normalize_output(text)

    item["id"] = context.aws_request_id

    item = convert_numbers(item)


    return response(200, {"output": item})


def save_handler(event, context, user_id):
    body = parse_body(event)

    item = body.get("item") or body.get("output")

    if not item:
        return response(400, {"error": "Item is required"})

    if not isinstance(item, dict):
        return response(400, {"error": "Item must be a JSON object"})

    if "id" not in item:
        item["id"] = context.aws_request_id

    item["userId"] = user_id

    item = convert_numbers(item)

    table.put_item(Item=item)

    return response(200, {"message": "Saved successfully", "item": item})


def saved_handler(user_id):
    result = table.query(
        KeyConditionExpression=Key("userId").eq(user_id)
    )

    items = result.get("Items", [])

    return response(200, {"items": items})


def get_user_id(event):
    authorizer = event.get("requestContext", {}).get("authorizer", {})

    claims = authorizer.get("claims")
    if claims:
        return claims.get("sub")

    jwt_claims = authorizer.get("jwt", {}).get("claims")
    if jwt_claims:
        return jwt_claims.get("sub")

    return None


def parse_body(event):
    body = event.get("body", {})

    if isinstance(body, str):
        if not body:
            return {}
        return json.loads(body)

    if isinstance(body, dict):
        return body

    return {}


def normalize_output(text):
    text = re.sub(r"```json|```", "", text.strip(), flags=re.IGNORECASE)

    start = text.find("{")
    end = text.rfind("}")

    if start != -1 and end != -1 and end > start:
        text = text[start:end + 1]

    try:
        obj = json.loads(text)
    except Exception:
        return {"output": text}

    if isinstance(obj, dict):
        return obj

    return {"output": obj}


def convert_numbers(value):
    if isinstance(value, float):
        return Decimal(str(value))

    if isinstance(value, dict):
        return {str(k): convert_numbers(v) for k, v in value.items()}

    if isinstance(value, list):
        return [convert_numbers(v) for v in value]

    return value


def json_safe(value):
    if isinstance(value, Decimal):
        return float(value)

    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}

    if isinstance(value, list):
        return [json_safe(v) for v in value]

    return value


def response(status, body):
    return {
        "statusCode": status,
        "headers": {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": "OPTIONS,GET,POST"
        },
        "body": json.dumps(json_safe(body))
    }
