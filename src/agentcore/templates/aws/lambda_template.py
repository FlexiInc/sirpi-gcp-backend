"""
AWS Lambda template generator.
Example of adding a new deployment target.
"""

from typing import Dict
from src.agentcore.templates.registry import TemplateGenerator, TemplateMetadata, DeploymentPlatform
from src.agentcore.models import RepositoryContext


class LambdaTemplateGenerator:
    """Generate Terraform for AWS Lambda + API Gateway deployment."""
    
    def generate(
        self,
        analysis_result,  # AnalysisResult from code_analyzer_agent
        project_id: str,
        repo_full_name: str | None = None,
        **kwargs
    ) -> Dict[str, str]:
        """Generate Lambda Terraform files."""
        app_name = repo_full_name.split("/")[-1].lower() if repo_full_name else f"app-{project_id[:8]}"
        runtime = self._map_runtime(analysis_result.language, analysis_result.runtime_version)
        
        files = {}
        
        # main.tf
        files['main.tf'] = f'''terraform {{
  required_version = ">= 1.5.0"
  required_providers {{
    aws = {{
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }}
  }}
}}

provider "aws" {{
  region = var.region
}}

# Lambda Function
resource "aws_lambda_function" "{app_name}" {{
  function_name = var.app_name
  role          = aws_iam_role.lambda_exec.arn
  handler       = "lambda_function.lambda_handler"
  runtime       = "{runtime}"
  
  filename         = "lambda_function.zip"
  source_code_hash = filebase64sha256("lambda_function.zip")
  
  timeout     = 30
  memory_size = 512
  
  environment {{
    variables = {{
      ENVIRONMENT = var.environment
    }}
  }}
  
  tags = {{
    Name        = var.app_name
    Environment = var.environment
  }}
}}

# API Gateway
resource "aws_apigatewayv2_api" "main" {{
  name          = "${{var.app_name}}-api"
  protocol_type = "HTTP"
}}

resource "aws_apigatewayv2_integration" "lambda" {{
  api_id             = aws_apigatewayv2_api.main.id
  integration_type   = "AWS_PROXY"
  integration_uri    = aws_lambda_function.{app_name}.invoke_arn
  integration_method = "POST"
}}

resource "aws_apigatewayv2_route" "default" {{
  api_id    = aws_apigatewayv2_api.main.id
  route_key = "$default"
  target    = "integrations/${{aws_apigatewayv2_integration.lambda.id}}"
}}

resource "aws_apigatewayv2_stage" "prod" {{
  api_id      = aws_apigatewayv2_api.main.id
  name        = "$default"
  auto_deploy = true
}}

resource "aws_lambda_permission" "api_gateway" {{
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.{app_name}.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${{aws_apigatewayv2_api.main.execution_arn}}/*/*"
}}
'''
        
        # variables.tf
        files['variables.tf'] = f'''variable "region" {{
  description = "AWS region"
  type        = string
  default     = "us-west-2"
}}

variable "app_name" {{
  description = "Application name"
  type        = string
  default     = "{app_name}"
}}

variable "environment" {{
  description = "Environment (dev, staging, prod)"
  type        = string
  default     = "production"
}}
'''
        
        # outputs.tf
        files['outputs.tf'] = '''output "api_endpoint" {
  description = "API Gateway endpoint URL"
  value       = aws_apigatewayv2_stage.prod.invoke_url
}

output "lambda_function_name" {
  description = "Lambda function name"
  value       = aws_lambda_function.''' + app_name + '''.function_name
}
'''
        
        # iam.tf
        files['iam.tf'] = '''resource "aws_iam_role" "lambda_exec" {
  name = "${var.app_name}-lambda-role"
  
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "lambda.amazonaws.com"
      }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_policy" {
  role       = aws_iam_role.lambda_exec.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}
'''
        
        return files
    
    def _map_runtime(self, language: str, runtime_version: str | None) -> str:
        """Map detected language to Lambda runtime."""
        lang = language.lower()
        if "python" in lang:
            return "python3.12"
        elif "node" in lang or "javascript" in lang:
            return "nodejs20.x"
        elif "go" in lang:
            return "go1.x"
        else:
            return "python3.12"  # Default fallback
    
    def get_metadata(self) -> TemplateMetadata:
        """Template metadata."""
        return TemplateMetadata(
            name="AWS Lambda + API Gateway",
            platform=DeploymentPlatform.AWS_LAMBDA,
            cloud_provider="aws",
            description="Serverless function deployment with HTTP API (best for APIs and webhooks)",
            requires_load_balancer=False,
            requires_container_registry=False,
            supports_autoscaling=True,
            min_cost_estimate_monthly=5.0,  # ~$5/mo for moderate traffic
            difficulty="beginner"
        )


# Singleton instance
lambda_template = LambdaTemplateGenerator()
