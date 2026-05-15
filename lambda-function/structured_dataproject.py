import base64
import io
import json
import os
import re
import zipfile
from datetime import datetime, timezone
from decimal import Decimal
from xml.sax.saxutils import escape

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

bedrock = boto3.client("bedrock-runtime")
dynamodb = boto3.resource("dynamodb")

MODEL_ID = "amazon.nova-micro-v1:0"
TABLE_NAME = os.environ.get("DYNAMODB_TABLE", "structured_dataproject")
SQS_QUEUE_URL = os.environ.get("SQS_QUEUE_URL", "")
SQS_QUEUE_ARN = os.environ.get("SQS_QUEUE_ARN", "")

table = dynamodb.Table(TABLE_NAME)

SYSTEM_PROMPT = """
Convert the input into valid JSON only.

Rules:
- Return ONLY JSON
- No markdown
- No explanations
- Response must start with { and end with }
"""

COMMON_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Access-Control-Allow-Methods": "OPTIONS,GET,POST",
}

REVIEW_OUTPUT_TYPE = "reviewoutput"
SAVED_ITEM_TYPE = "saved"
HIDDEN_RESPONSE_FIELDS = {"userId", "createdAt", "updatedAt", "itemType"}


def lambda_handler(event, context):
    try:
        # New trigger: SQS invokes the Lambda with Records.
        if is_sqs_event(event):
            return sqs_handler(event, context)

        # Existing trigger: API Gateway invokes the Lambda for GET/POST routes.
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

        # Kept for local/manual testing. In Terraform, POST /generate now sends to SQS.
        if path.endswith("/generate") and method == "POST":
            return generate_handler(event, context, user_id)

        if path.endswith("/reviewoutput") and method == "GET":
            return reviewoutput_handler(event, user_id)

        if path.endswith("/save") and method == "POST":
            return save_handler(event, context, user_id)

        if path.endswith("/saved") and method == "GET":
            return saved_handler(user_id)

        if path.endswith("/delete") and method == "POST":
            return delete_handler(event, user_id)

        if path.endswith("/download") and method == "GET":
            return download_handler(user_id)

        return response(404, {"error": "Route not found"})

    except Exception as e:
        return response(500, {"error": str(e)})


def is_sqs_event(event):
    records = event.get("Records") if isinstance(event, dict) else None
    if not records:
        return False

    return any(record.get("eventSource") == "aws:sqs" for record in records)


def sqs_handler(event, context):
    """
    Processes SQS messages created by POST /generate.

    Expected SQS message body from API Gateway:
    {
      "id": "<frontend-request-id>",
      "prompt": "..."
    }

    The frontend includes the same JWT that API Gateway already validated in
    the SQS JSON message body as authToken. Lambda decodes that JWT to recover
    the Cognito sub/userId for DynamoDB partitioning.
    """
    batch_item_failures = []
    processed = []

    for record in event.get("Records", []):
        message_id = record.get("messageId") or context.aws_request_id

        try:
            result = process_sqs_record(record, context)
            processed.append(result)
        except Exception as exc:
            print(f"Failed to process SQS message {message_id}: {exc}")
            batch_item_failures.append({"itemIdentifier": message_id})

    # Requires function_response_types = ["ReportBatchItemFailures"] in Terraform.
    return {
        "batchItemFailures": batch_item_failures,
        "processed": len(processed),
        "failed": len(batch_item_failures),
    }


def process_sqs_record(record, context):
    message = parse_sqs_record(record)
    user_id = str(message.get("userId") or "").strip()
    user_input = str(message.get("prompt") or "").strip()

    if not user_id:
        raise ValueError(
            "SQS message is missing userId. Include authToken/idToken in the "
            "queued JSON body, or include userId in the message body."
        )

    if not user_input:
        raise ValueError("SQS message is missing prompt")

    
    today = datetime.now(timezone.utc).date().isoformat()
    user_items = query_user_items(user_id)
    today_requests = [
        item for item in user_items
        if item.get("itemType") == REVIEW_OUTPUT_TYPE and
        item.get("createdAt", "").startswith(today)
    ]
    if len(today_requests) >= 50:
        raise ValueError("You have exceeded your daily limit. Please try again tomorrow.")

    word_count = len(user_input.split())
    if word_count > 500:
        raise ValueError("Prompt exceeds 500 words limit")

    if len(user_input) > 8000:
        raise ValueError("Prompt exceeds 8000 characters limit")

    output_item = generate_item(user_input)

    now = datetime.now(timezone.utc).isoformat()
    item_id = str(
        message.get("id")
        or record.get("messageId")
        or context.aws_request_id
    )

    item = {
        "userId": user_id,
        "id": item_id,
        "itemType": REVIEW_OUTPUT_TYPE,
        "status": "completed",
        "prompt": user_input,
        "output": output_item,
        "createdAt": now,
        "updatedAt": now,
        "source": "sqs",
        "sqsMessageId": record.get("messageId", ""),
        "lambdaRequestId": context.aws_request_id,
    }

    item = convert_numbers(item)
    table.put_item(Item=item)

    return {"id": item_id, "userId": user_id, "status": "completed"}


def parse_sqs_record(record):
    body = record.get("body", "")
    decoded = parse_json_value(body)

    if not isinstance(decoded, dict):
        return {
            "userId": get_sqs_message_attribute(record, "userId"),
            "prompt": str(decoded or body),
        }

    message_attributes = record.get("messageAttributes", {}) or {}
    attribute_user_id = get_sqs_message_attribute(record, "userId")
    authorization_token = (
        get_sqs_message_attribute(record, "Authorization")
        or decoded.get("authorization")
        or decoded.get("Authorization")
        or decoded.get("authToken")
        or decoded.get("idToken")
    )

    user_id = (
        decoded.get("userId")
        or decoded.get("user_id")
        or attribute_user_id
        or get_user_id_from_authorization_header(authorization_token)
    )

    inner_body = (
        decoded.get("body")
        or decoded.get("requestBody")
        or decoded.get("payload")
        or decoded
    )

    if isinstance(inner_body, str):
        inner_body = parse_json_value(inner_body)

    if not isinstance(inner_body, dict):
        inner_body = {"prompt": str(inner_body or "")}

    prompt = (
        inner_body.get("prompt")
        or inner_body.get("input")
        or inner_body.get("message")
        or decoded.get("prompt")
        or decoded.get("input")
        or decoded.get("message")
    )

    item_id = decoded.get("id") or inner_body.get("id")

    return {
        "userId": user_id,
        "prompt": prompt,
        "id": item_id,
        "raw": decoded,
        "messageAttributes": message_attributes,
    }


def get_sqs_message_attribute(record, name):
    attributes = record.get("messageAttributes", {}) or {}
    value = None

    for key, candidate in attributes.items():
        if str(key).lower() == str(name).lower():
            value = candidate
            break

    if not isinstance(value, dict):
        return None

    return value.get("stringValue") or value.get("StringValue")


def get_user_id_from_authorization_header(value):
    if not value:
        return None

    token = str(value).strip()

    if token.lower().startswith("bearer "):
        token = token[7:].strip()

    claims = decode_jwt_claims_without_verification(token)

    if not isinstance(claims, dict):
        return None

    return claims.get("sub")


def decode_jwt_claims_without_verification(token):
    """
    Decode JWT claims without re-verifying the signature.

    This token came from the Authorization header on a route that API Gateway
    already validated with the JWT authorizer before sending the message to SQS.
    """
    try:
        parts = str(token).split(".")
        if len(parts) < 2:
            return None

        payload = parts[1]
        padding = "=" * (-len(payload) % 4)
        decoded = base64.urlsafe_b64decode((payload + padding).encode("utf-8"))
        return json.loads(decoded.decode("utf-8"))
    except Exception as exc:
        print(f"Could not decode JWT claims from SQS Authorization attribute: {exc}")
        return None


def generate_handler(event, context, user_id):
    body = parse_body(event)
    user_input = str(body.get("prompt", "")).strip()

    if not user_input:
        return response(400, {"error": "Prompt is required"})

    if len(user_input) > 8000:
        return response(400, {"error": "Prompt exceeds 8000 characters limit"})

    item = generate_item(user_input)
    item["id"] = context.aws_request_id
    item = convert_numbers(item)

    return response(200, {"output": item})


def generate_item(user_input):
    r = bedrock.converse(
        modelId=MODEL_ID,
        system=[{"text": SYSTEM_PROMPT}],
        messages=[
            {
                "role": "user",
                "content": [{"text": user_input}],
            }
        ],
        inferenceConfig={"maxTokens": 300, "temperature": 0},
    )

    text = r["output"]["message"]["content"][0]["text"]
    return normalize_output(text)


def reviewoutput_handler(event, user_id):
    params = event.get("queryStringParameters") or {}
    requested_id = str(params.get("id") or "").strip()
    limit = parse_positive_int(params.get("limit"), default=25, maximum=100)

    items = get_review_outputs(user_id)

    if requested_id:
        items = [item for item in items if str(item.get("id")) == requested_id]

    items.sort(key=lambda item: item.get("createdAt", ""), reverse=True)

    return response(200, {"items": items[:limit]})


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
    item.setdefault("itemType", SAVED_ITEM_TYPE)
    item.setdefault("createdAt", datetime.now(timezone.utc).isoformat())

    item = convert_numbers(item)

    table.put_item(Item=item)

    return response(200, {
        "message": "Saved successfully",
        "item": hide_internal_fields(item),
    })


def saved_handler(user_id):
    items = get_saved_items(user_id)
    visible_items = [hide_internal_fields(item) for item in items]
    return response(200, {"items": visible_items})


def delete_handler(event, user_id):
    body = parse_body(event)
    item_id = str(body.get("id", "")).strip()

    if not item_id:
        return response(400, {"error": "Item id is required"})

    try:
        table.delete_item(
            Key={
                "userId": user_id,
                "id": item_id,
            },
            ConditionExpression="attribute_exists(userId) AND attribute_exists(id)",
        )
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return response(404, {"error": "Saved item not found"})
        raise

    return response(200, {"message": "Deleted successfully", "id": item_id})


def download_handler(user_id):
    items = get_saved_items(user_id)
    xlsx_bytes = build_xlsx(items)
    filename = f"saved-data-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.xlsx"

    return {
        "statusCode": 200,
        "headers": {
            **COMMON_HEADERS,
            "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
        "isBase64Encoded": True,
        "body": base64.b64encode(xlsx_bytes).decode("utf-8"),
    }


def query_user_items(user_id):
    items = []
    query_args = {
        "KeyConditionExpression": Key("userId").eq(user_id)
    }

    while True:
        result = table.query(**query_args)
        items.extend(result.get("Items", []))

        last_key = result.get("LastEvaluatedKey")
        if not last_key:
            break

        query_args["ExclusiveStartKey"] = last_key

    return items


def get_saved_items(user_id):
    items = query_user_items(user_id)

    # Keep old saved records that do not have itemType, but exclude generated review outputs.
    return [
        item for item in items
        if item.get("itemType") in (None, "", SAVED_ITEM_TYPE)
    ]


def get_review_outputs(user_id):
    items = query_user_items(user_id)
    return [item for item in items if item.get("itemType") == REVIEW_OUTPUT_TYPE]


def hide_internal_fields(item):
    if not isinstance(item, dict):
        return item

    return {
        key: value
        for key, value in item.items()
        if key not in HIDDEN_RESPONSE_FIELDS
    }


def build_xlsx(items):
    flattened_items = [flatten_item(item) for item in items]

    columns = []
    for item in flattened_items:
        for key in item.keys():
            if key not in columns:
                columns.append(key)

    if not columns:
        columns = ["message"]
        flattened_items = [{"message": "No saved items yet."}]

    sheet_xml = build_sheet_xml(columns, flattened_items)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as xlsx:
        xlsx.writestr("[Content_Types].xml", content_types_xml())
        xlsx.writestr("_rels/.rels", package_relationships_xml())
        xlsx.writestr("xl/workbook.xml", workbook_xml())
        xlsx.writestr("xl/_rels/workbook.xml.rels", workbook_relationships_xml())
        xlsx.writestr("xl/worksheets/sheet1.xml", sheet_xml)

    return buffer.getvalue()


def flatten_item(value, parent_key=""):
    flattened = {}

    if not isinstance(value, dict):
        return {parent_key or "value": value}

    for key, child_value in value.items():
        if key in HIDDEN_RESPONSE_FIELDS:
            continue

        column_name = f"{parent_key}.{key}" if parent_key else str(key)

        if isinstance(child_value, dict):
            flattened.update(flatten_item(child_value, column_name))
        elif isinstance(child_value, list):
            flattened[column_name] = json.dumps(json_safe(child_value), ensure_ascii=False)
        else:
            flattened[column_name] = child_value

    return flattened


def build_sheet_xml(columns, rows):
    sheet_rows = [build_row(1, columns)]

    for index, row in enumerate(rows, start=2):
        values = [row.get(column, "") for column in columns]
        sheet_rows.append(build_row(index, values))

    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetData>'
        + "".join(sheet_rows) +
        '</sheetData>'
        '</worksheet>'
    )


def build_row(row_number, values):
    cells = []
    for column_index, value in enumerate(values, start=1):
        cell_ref = f"{column_letter(column_index)}{row_number}"
        cells.append(build_cell(cell_ref, value))

    return f'<row r="{row_number}">{"".join(cells)}</row>'


def build_cell(cell_ref, value):
    value = json_safe(value)

    if value is None:
        return f'<c r="{cell_ref}" t="inlineStr"><is><t></t></is></c>'

    if isinstance(value, bool):
        return f'<c r="{cell_ref}" t="b"><v>{1 if value else 0}</v></c>'

    if isinstance(value, (int, float)):
        return f'<c r="{cell_ref}"><v>{value}</v></c>'

    text = escape(str(value))
    return f'<c r="{cell_ref}" t="inlineStr"><is><t>{text}</t></is></c>'


def column_letter(index):
    letters = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def content_types_xml():
    return '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>'''


def package_relationships_xml():
    return '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>'''


def workbook_xml():
    return '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="Saved Data" sheetId="1" r:id="rId1"/>
  </sheets>
</workbook>'''


def workbook_relationships_xml():
    return '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>'''


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

    if event.get("isBase64Encoded") and isinstance(body, str):
        body = base64.b64decode(body).decode("utf-8")

    if isinstance(body, str):
        if not body:
            return {}
        return json.loads(body)

    if isinstance(body, dict):
        return body

    return {}


def parse_json_value(value):
    if isinstance(value, (dict, list)):
        return value

    if value is None:
        return None

    if not isinstance(value, str):
        return value

    value = value.strip()
    if not value:
        return None

    try:
        return json.loads(value)
    except Exception:
        return value


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


def parse_positive_int(value, default, maximum):
    try:
        parsed = int(value)
    except Exception:
        return default

    if parsed < 1:
        return default

    return min(parsed, maximum)


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
        return int(value) if value % 1 == 0 else float(value)

    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}

    if isinstance(value, list):
        return [json_safe(v) for v in value]

    return value


def response(status, body):
    return {
        "statusCode": status,
        "headers": COMMON_HEADERS,
        "body": json.dumps(json_safe(body)),
    }
