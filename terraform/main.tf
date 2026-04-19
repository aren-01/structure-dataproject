terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

variable "aws_region" {
  description = "AWS region to deploy resources into"
  type        = string
  default     = "us-east-1"
}

variable "bucket_name" {
  description = "Name for the public S3 bucket"
  type        = string
}

resource "aws_s3_bucket" "website_bucket" {
  bucket = var.bucket_name
}

resource "aws_s3_bucket_website_configuration" "website_config" {
  bucket = aws_s3_bucket.website_bucket.id

  index_document {
    suffix = "index.html"
  }
}

resource "aws_s3_bucket_public_access_block" "public_access" {
  bucket = aws_s3_bucket.website_bucket.id

  block_public_acls       = false
  block_public_policy     = false
  ignore_public_acls      = false
  restrict_public_buckets = false
}

resource "aws_s3_bucket_policy" "public_read_policy" {
  bucket = aws_s3_bucket.website_bucket.id

  depends_on = [
    aws_s3_bucket_public_access_block.public_access
  ]

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "PublicReadGetObject"
        Effect    = "Allow"
        Principal = "*"
        Action    = [
          "s3:GetObject"
        ]
        Resource = "${aws_s3_bucket.website_bucket.arn}/*"
      }
    ]
  })
}

resource "aws_dynamodb_table" "structured_table" {
  name         = "structured_dataproject"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"

  attribute {
    name = "id"
    type = "S"
  }
}

resource "aws_iam_role" "lambda_role" {
  name = "structured_function_role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_basic_execution" {
  role       = aws_iam_role.lambda_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "bedrock_access" {
  name = "structured-function-bedrock-access"
  role = aws_iam_role.lambda_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "bedrock:*",
          "bedrock-runtime:*"
        ]
        Resource = "*"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "dynamodb_full_access" {
  role       = aws_iam_role.lambda_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonDynamoDBFullAccess"
}

data "archive_file" "lambda_zip" {
  type        = "zip"
  output_path = "${path.module}/structured_function.zip"

  source {
    filename = "lambda_function.py"
    content  = <<-PYTHON
      def lambda_handler(event, context):
          return {
              "statusCode": 200,
              "body": "Replace this Lambda code later"
          }
    PYTHON
  }
}

resource "aws_lambda_function" "structured_function" {
  function_name    = "structured_function"
  role             = aws_iam_role.lambda_role.arn
  handler          = "structured_dataproject.lambda_handler"
  runtime          = "python3.12"
  filename         = data.archive_file.lambda_zip.output_path
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256
  timeout          = 30

  environment {
    variables = {
      DYNAMODB_TABLE = aws_dynamodb_table.structured_table.name
    }
  }
}

# -----------------------------
# Cognito User Pool for SPA login
# -----------------------------
resource "aws_cognito_user_pool" "structured_user_pool" {
  name = "structured-dataproject-pool01"

  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  verification_message_template {
    default_email_option = "CONFIRM_WITH_CODE"
    email_subject        = "Your verification code – Structure Your Data"
    email_message        = "Your verification code is {####}"
  }

  schema {
    name                = "email"
    attribute_data_type = "String"
    required            = true
    mutable             = true

    string_attribute_constraints {
      min_length = 5
      max_length = 2048
    }
  }

  schema {
    name                = "name"
    attribute_data_type = "String"
    required            = true
    mutable             = true

    string_attribute_constraints {
      min_length = 1
      max_length = 2048
    }
  }
}

resource "aws_cognito_user_pool_client" "structured_spa_client" {
  name         = "structured-dataproject-pool01-spa-client"
  user_pool_id = aws_cognito_user_pool.structured_user_pool.id

  generate_secret = false

  explicit_auth_flows = [
    "ALLOW_USER_PASSWORD_AUTH",
    "ALLOW_USER_SRP_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH"
  ]

  prevent_user_existence_errors = "ENABLED"

  supported_identity_providers = ["COGNITO"]

  read_attributes = [
    "email",
    "name"
  ]

  write_attributes = [
    "email",
    "name"
  ]
}

# -----------------------------
# HTTP API
# -----------------------------
resource "aws_apigatewayv2_api" "http_api" {
  name            = "structured_dataproject"
  protocol_type   = "HTTP"
  ip_address_type = "ipv4"

  cors_configuration {
    allow_origins = ["*"]
    allow_methods = ["POST", "OPTIONS"]
    allow_headers = ["content-type", "authorization"]
  }
}

# JWT authorizer using Cognito
resource "aws_apigatewayv2_authorizer" "cognito_authorizer" {
  api_id          = aws_apigatewayv2_api.http_api.id
  name            = "structured-cognito-authorizer"
  authorizer_type = "JWT"

  identity_sources = [
    "$request.header.Authorization"
  ]

  jwt_configuration {
    audience = [aws_cognito_user_pool_client.structured_spa_client.id]
    issuer   = "https://cognito-idp.${var.aws_region}.amazonaws.com/${aws_cognito_user_pool.structured_user_pool.id}"
  }
}

resource "aws_apigatewayv2_integration" "lambda_integration" {
  api_id                 = aws_apigatewayv2_api.http_api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.structured_function.invoke_arn
  integration_method     = "POST"
  payload_format_version = "2.0"
}

# Protected route
resource "aws_apigatewayv2_route" "post_generate" {
  api_id    = aws_apigatewayv2_api.http_api.id
  route_key = "POST /generate"
  target    = "integrations/${aws_apigatewayv2_integration.lambda_integration.id}"

  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito_authorizer.id
}

resource "aws_lambda_permission" "allow_apigw_invoke" {
  statement_id  = "AllowExecutionFromAPIGateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.structured_function.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http_api.execution_arn}/*/*"
}

resource "aws_apigatewayv2_stage" "project_stage" {
  api_id      = aws_apigatewayv2_api.http_api.id
  name        = "project-stage"
  auto_deploy = true
}

output "s3_website_url" {
  value = aws_s3_bucket_website_configuration.website_config.website_endpoint
}

output "api_url" {
  value = aws_apigatewayv2_api.http_api.api_endpoint
}

output "lambda_name" {
  value = aws_lambda_function.structured_function.function_name
}

output "dynamodb_table_name" {
  value = aws_dynamodb_table.structured_table.name
}

output "cognito_user_pool_id" {
  value = aws_cognito_user_pool.structured_user_pool.id
}

output "cognito_user_pool_client_id" {
  value = aws_cognito_user_pool_client.structured_spa_client.id
}


output "cognito_region" {
  value = var.aws_region
}
