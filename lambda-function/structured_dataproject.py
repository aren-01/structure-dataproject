import json
import re
import boto3
from decimal import Decimal

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
        body = parse_body(event)
        user_input = body.get("prompt", "").strip()

        if not user_input:
            return response(400, {"error": "Prompt is required"})

        r = bedrock.converse(
            modelId=MODEL_ID,
            system=[{"text": SYSTEM_PROMPT}],
            messages=[{
                "role": "user",
                "content": [{"text": user_input}]
            }],
            inferenceConfig={"maxTokens": 300, "temperature": 0}
        )

        text = r["output"]["message"]["content"][0]["text"]
        item = normalize_output(text)

       
        item["id"] = context.aws_request_id

        
        item = convert_numbers(item)

        table.put_item(Item=item)

        return response(200, {"output": item})

    except Exception as e:
        return response(500, {"error": str(e)})

def parse_body(event):
    body = event.get("body", {})
    if isinstance(body, str):
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
            "Access-Control-Allow-Headers": "*",
            "Access-Control-Allow-Methods": "OPTIONS,POST"
        },
        "body": json.dumps(json_safe(body))
    }