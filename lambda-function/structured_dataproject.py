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

        if path.endswith("/delete") and method == "POST":
            return delete_handler(event, user_id)

        if path.endswith("/download") and method == "GET":
            return download_handler(user_id)

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
    items = get_saved_items(user_id)
    return response(200, {"items": items})


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


def get_saved_items(user_id):
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


def build_xlsx(items):
    flattened_items = [flatten_item(item) for item in items]

    columns = []
    for preferred in ["id", "name", "title", "major"]:
        if any(preferred in item for item in flattened_items):
            columns.append(preferred)

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
        if key == "userId":
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
        "body": json.dumps(json_safe(body))
    }
