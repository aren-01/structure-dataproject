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

# Ask for the AWS region
variable "aws_region" {
  description = "AWS region to deploy resources into"
  type        = string
  default     = "us-east-1"
}

# Ask for the S3 bucket name
variable "bucket_name" {
  description = "Name for the public S3 bucket"
  type        = string
}

# Create the S3 bucket
resource "aws_s3_bucket" "website_bucket" {
  bucket = var.bucket_name
}

# Enable static website hosting and use index.html as the home page. index.html will be uploaded later.
resource "aws_s3_bucket_website_configuration" "website_config" {
  bucket = aws_s3_bucket.website_bucket.id

  index_document {
    suffix = "index.html"
  }
}

# Open the bucket for public access
resource "aws_s3_bucket_public_access_block" "public_access" {
  bucket = aws_s3_bucket.website_bucket.id

  block_public_acls       = false
  block_public_policy     = false
  ignore_public_acls      = false
  restrict_public_buckets = false
}

# Allow everyone to read files on the web
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

# Create the DynamoDB table
resource "aws_dynamodb_table" "structured_table" {
  name         = "structured_dataproject"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"

  attribute {
    name = "id"
    type = "S"
  }
}

# Create the IAM role for the Lambda function
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

# Allow the Lambda function to write logs
resource "aws_iam_role_policy_attachment" "lambda_basic_execution" {
  role       = aws_iam_role.lambda_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# Allow the Lambda function to use Amazon Bedrock
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

# Allow the Lambda function full access to DynamoDB
resource "aws_iam_role_policy_attachment" "dynamodb_full_access" {
  role       = aws_iam_role.lambda_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonDynamoDBFullAccess"
}

# Create a small placeholder zip for Lambda
# Replace this code with the "structured_dataproject.py" AFTER DEPLOYMENT!
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

# Create the Lambda function named structured_function
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

# Create the HTTP API named structured_dataproject
resource "aws_apigatewayv2_api" "http_api" {
  name            = "structured_dataproject"
  protocol_type   = "HTTP"
  ip_address_type = "ipv4"

  # Allow CORS for POST and OPTIONS requests
  cors_configuration {
    allow_origins = ["*"]
    allow_methods = ["POST", "OPTIONS"]
    allow_headers = ["content-type"]
  }
}

# Connect the API Gateway to the Lambda function. In that way, user interface will display the output.
resource "aws_apigatewayv2_integration" "lambda_integration" {
  api_id                 = aws_apigatewayv2_api.http_api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.structured_function.invoke_arn
  integration_method     = "POST"
  payload_format_version = "2.0"
}

# Create the POST /generate route and attach it to the Lambda function
resource "aws_apigatewayv2_route" "post_generate" {
  api_id    = aws_apigatewayv2_api.http_api.id
  route_key = "POST /generate"
  target    = "integrations/${aws_apigatewayv2_integration.lambda_integration.id}"
}



# Allow API Gateway to invoke the Lambda function. It is important when user types an input.
resource "aws_lambda_permission" "allow_apigw_invoke" {
  statement_id  = "AllowExecutionFromAPIGateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.structured_function.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http_api.execution_arn}/*/*"
}

# Create a deployment for the API
resource "aws_apigatewayv2_deployment" "api_deployment" {
  api_id = aws_apigatewayv2_api.http_api.id

  depends_on = [
    aws_apigatewayv2_route.post_generate,
  ]
}

# Create the stage named project-stage and disable auto deploy
resource "aws_apigatewayv2_stage" "project_stage" {
  api_id        = aws_apigatewayv2_api.http_api.id
  name          = "project-stage"
  deployment_id = aws_apigatewayv2_deployment.api_deployment.id
  auto_deploy   = true
}

# Show the S3 website URL after deployment
output "s3_website_url" {
  value = aws_s3_bucket_website_configuration.website_config.website_endpoint
}

# Show the API base URL after deployment
output "api_url" {
  value = aws_apigatewayv2_api.http_api.api_endpoint
}

# Show the Lambda function name after deployment
output "lambda_name" {
  value = aws_lambda_function.structured_function.function_name
}

# Show the DynamoDB table name after deployment
output "dynamodb_table_name" {
  value = aws_dynamodb_table.structured_table.name
}
