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
  description = "Name for the S3 bucket"
  type        = string
}

resource "aws_s3_bucket" "website_bucket" {
  bucket = var.bucket_name
}

resource "aws_s3_bucket_public_access_block" "website_bucket" {
  bucket = aws_s3_bucket.website_bucket.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "website_bucket" {
  bucket = aws_s3_bucket.website_bucket.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "website_bucket" {
  bucket = aws_s3_bucket.website_bucket.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
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
    filename = "structured_dataproject.py"
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

resource "aws_cloudfront_origin_access_control" "s3_oac" {
  name                              = "structured-dataproject-s3-oac"
  description                       = "OAC for structured-dataproject S3 origin"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_function" "remove_file_extensions" {
  name    = "remove-file-extensions"
  runtime = "cloudfront-js-2.0"
  comment = "Rewrite clean URLs to HTML files"
  publish = true
  code    = <<-JS
    function handler(event) {
        var request = event.request;
        var uri = request.uri;

        // Home page
        if (uri === "/") {
            request.uri = "/index.html";
        }
        // Login page
        else if (uri === "/login") {
            request.uri = "/login.html";
        }

        return request;
    }
  JS
}

resource "aws_cloudfront_cache_policy" "optimized" {
  name        = "structured-dataproject-cache-policy"
  comment     = "Recommended cache policy for static website"
  default_ttl = 86400
  max_ttl     = 31536000
  min_ttl     = 0

  parameters_in_cache_key_and_forwarded_to_origin {
    cookies_config {
      cookie_behavior = "none"
    }

    headers_config {
      header_behavior = "none"
    }

    query_strings_config {
      query_string_behavior = "none"
    }

    enable_accept_encoding_brotli = true
    enable_accept_encoding_gzip   = true
  }
}

resource "aws_cloudfront_distribution" "structured_dataproject" {
  enabled             = true
  comment             = "structured-dataproject"
  default_root_object = "index.html"
  price_class         = "PriceClass_All"

  origin {
    domain_name              = aws_s3_bucket.website_bucket.bucket_regional_domain_name
    origin_id                = "structured-dataproject-s3-origin"
    origin_access_control_id = aws_cloudfront_origin_access_control.s3_oac.id
  }

  default_cache_behavior {
    target_origin_id       = "structured-dataproject-s3-origin"
    viewer_protocol_policy = "redirect-to-https"
    compress               = true

    allowed_methods = ["GET", "HEAD", "OPTIONS"]
    cached_methods  = ["GET", "HEAD"]

    cache_policy_id = aws_cloudfront_cache_policy.optimized.id

    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.remove_file_extensions.arn
    }
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }

  depends_on = [
    aws_cloudfront_function.remove_file_extensions
  ]
}

data "aws_iam_policy_document" "website_bucket_policy" {
  statement {
    sid    = "AllowCloudFrontServicePrincipalReadOnly"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }

    actions = [
      "s3:GetObject"
    ]

    resources = [
      "${aws_s3_bucket.website_bucket.arn}/*"
    ]

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.structured_dataproject.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "website_bucket_policy" {
  bucket = aws_s3_bucket.website_bucket.id
  policy = data.aws_iam_policy_document.website_bucket_policy.json
}

output "cloudfront_distribution_id" {
  value = aws_cloudfront_distribution.structured_dataproject.id
}

output "cloudfront_domain_name" {
  value = aws_cloudfront_distribution.structured_dataproject.domain_name
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

output "bucket_name" {
  value = aws_s3_bucket.website_bucket.id
}
