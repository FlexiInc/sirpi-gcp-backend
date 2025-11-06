"""
Cloud-Agnostic CI/CD Generator.
Generates GitHub Actions workflows for AWS or GCP deployments.
"""

import logging
from typing import Literal
from .code_analyzer_agent import AnalysisResult


CloudProvider = Literal["aws", "gcp"]


class CICDGeneratorAgent:
    """
    Template-based GitHub Actions workflow generator.
    Supports GCP Cloud Run and AWS ECS Fargate deployments.
    """
    
    def __init__(self, cloud_provider: CloudProvider = "gcp"):
        """
        Initialize CI/CD generator.
        
        Args:
            cloud_provider: Target cloud (gcp or aws)
        """
        self.logger = logging.getLogger(__name__)
        self.cloud_provider = cloud_provider
    
    def _generate_gcp_workflow(self, service_name: str, analysis: AnalysisResult) -> str:
        """Generate GitHub Actions workflow for Google Cloud Run."""
        
        build_step = ""
        if analysis.build_command:
            build_step = f"""      - name: Build application
        run: {analysis.build_command}
        
"""
        
        workflow = f"""name: Deploy to Google Cloud Run

on:
  push:
    branches: [main]
  workflow_dispatch:

env:
  GCP_PROJECT_ID: ${{{{ secrets.GCP_PROJECT_ID }}}}
  GCP_REGION: us-central1
  SERVICE_NAME: {service_name}
  IMAGE_NAME: {service_name}

jobs:
  deploy:
    runs-on: ubuntu-latest
    
    permissions:
      contents: read
      id-token: write
    
    steps:
      - name: Checkout code
        uses: actions/checkout@v4
      
      - name: Authenticate to Google Cloud
        uses: google-github-actions/auth@v2
        with:
          credentials_json: ${{{{ secrets.GCP_SA_KEY }}}}
      
      - name: Set up Cloud SDK
        uses: google-github-actions/setup-gcloud@v2
      
{build_step}      - name: Build and push container
        run: |
          gcloud builds submit \\
            --tag us-central1-docker.pkg.dev/$GCP_PROJECT_ID/sirpi-deployments/$IMAGE_NAME:${{{{ github.sha }}}} \\
            --timeout=20m
      
      - name: Deploy to Cloud Run
        run: |
          gcloud run deploy $SERVICE_NAME \\
            --image us-central1-docker.pkg.dev/$GCP_PROJECT_ID/sirpi-deployments/$IMAGE_NAME:${{{{ github.sha }}}} \\
            --region $GCP_REGION \\
            --platform managed \\
            --allow-unauthenticated \\
            --port {analysis.exposed_port or 8080} \\
            --memory 512Mi \\
            --cpu 1 \\
            --min-instances 0 \\
            --max-instances 10
      
      - name: Get service URL
        run: |
          SERVICE_URL=$(gcloud run services describe $SERVICE_NAME --region $GCP_REGION --format='value(status.url)')
          echo "Deployed to: $SERVICE_URL"
"""
        return workflow
    
    def _generate_aws_workflow(self, service_name: str, analysis: AnalysisResult) -> str:
        """Generate GitHub Actions workflow for AWS ECS Fargate."""
        
        build_step = ""
        if analysis.build_command:
            build_step = f"""      - name: Build application
        run: {analysis.build_command}
        
"""
        
        workflow = f"""name: Deploy to AWS ECS

on:
  push:
    branches: [main]
  workflow_dispatch:

env:
  AWS_REGION: us-west-2
  ECR_REPOSITORY: {service_name}
  ECS_CLUSTER: {service_name}-cluster
  ECS_SERVICE: {service_name}
  ECS_TASK_DEFINITION: {service_name}

jobs:
  deploy:
    runs-on: ubuntu-latest
    
    steps:
      - name: Checkout code
        uses: actions/checkout@v4
      
      - name: Configure AWS credentials
        uses: aws-actions/configure-aws-credentials@v4
        with:
          aws-access-key-id: ${{{{ secrets.AWS_ACCESS_KEY_ID }}}}
          aws-secret-access-key: ${{{{ secrets.AWS_SECRET_ACCESS_KEY }}}}
          aws-region: ${{{{ env.AWS_REGION }}}}
      
      - name: Login to Amazon ECR
        id: login-ecr
        uses: aws-actions/amazon-ecr-login@v2
      
{build_step}      - name: Build and push Docker image
        env:
          ECR_REGISTRY: ${{{{ steps.login-ecr.outputs.registry }}}}
          IMAGE_TAG: ${{{{ github.sha }}}}
        run: |
          docker build -t $ECR_REGISTRY/$ECR_REPOSITORY:$IMAGE_TAG .
          docker push $ECR_REGISTRY/$ECR_REPOSITORY:$IMAGE_TAG
          echo "image=$ECR_REGISTRY/$ECR_REPOSITORY:$IMAGE_TAG" >> $GITHUB_OUTPUT
        id: build-image
      
      - name: Download task definition
        run: |
          aws ecs describe-task-definition \\
            --task-definition $ECS_TASK_DEFINITION \\
            --query taskDefinition > task-definition.json
      
      - name: Update task definition with new image
        id: task-def
        uses: aws-actions/amazon-ecs-render-task-definition@v1
        with:
          task-definition: task-definition.json
          container-name: {service_name}
          image: ${{{{ steps.build-image.outputs.image }}}}
      
      - name: Deploy to ECS
        uses: aws-actions/amazon-ecs-deploy-task-definition@v1
        with:
          task-definition: ${{{{ steps.task-def.outputs.task-definition }}}}
          service: ${{{{ env.ECS_SERVICE }}}}
          cluster: ${{{{ env.ECS_CLUSTER }}}}
          wait-for-service-stability: true
"""
        return workflow
    
    def generate(self, service_name: str, analysis: AnalysisResult) -> str:
        """
        Generate CI/CD workflow synchronously.
        
        Args:
            service_name: Service name
            analysis: Repository analysis results
            
        Returns:
            GitHub Actions workflow YAML
        """
        self.logger.info(
            f"Generating {self.cloud_provider.upper()} CI/CD workflow for {service_name}"
        )
        
        if self.cloud_provider == "gcp":
            workflow = self._generate_gcp_workflow(service_name, analysis)
        else:
            workflow = self._generate_aws_workflow(service_name, analysis)
        
        self.logger.info(f"Generated workflow ({len(workflow)} chars)")
        
        return workflow
