"""
Terraform backend configuration template.
Ensures state is stored in S3 with DynamoDB locking.
"""

from src.core.config import settings


def generate_backend_config(project_id: str, account_id: str = None) -> str:
    """
    Generate Terraform backend configuration for S3 state storage.
    
    This prevents the VPC limit issue by:
    - Storing state in S3 (Terraform knows what's already created)
    - Using DynamoDB for state locking (prevents concurrent applies)
    - Enabling versioning (can rollback if needed)
    
    Args:
        project_id: Unique project identifier
        account_id: User's AWS account ID (for bucket name suffix)
        
    Returns:
        backend.tf content
    """
    
    # Use account_id suffix if provided (for user's account)
    # Otherwise use base bucket name (for Sirpi's account)
    bucket_name = settings.s3_terraform_state_bucket
    if account_id:
        bucket_name = f"{bucket_name}-{account_id}"
    
    return f'''terraform {{
  backend "s3" {{
    bucket         = "{bucket_name}"
    key            = "states/{project_id}/terraform.tfstate"
    region         = "{settings.s3_region}"
    dynamodb_table = "{settings.dynamodb_terraform_lock_table}"
    encrypt        = true
  }}
}}

# State locking prevents concurrent terraform applies
# Versioning allows rollback to previous states
# Encryption protects sensitive data in state
'''


def generate_state_setup_script(project_id: str) -> str:
    """
    Generate script to initialize Terraform backend.
    User runs this once before first terraform apply.
    
    Args:
        project_id: Unique project identifier
        
    Returns:
        Shell script content
    """
    
    return f'''#!/bin/bash
# Terraform Backend Setup Script
# Run this ONCE before your first terraform apply

set -e

echo "🚀 Setting up Terraform backend for project: {project_id}"

# Check if AWS CLI is configured
if ! aws sts get-caller-identity > /dev/null 2>&1; then
  echo "❌ Error: AWS CLI not configured"
  echo "Run: aws configure"
  exit 1
fi

# Create S3 bucket for state (if doesn't exist)
echo "📦 Ensuring S3 state bucket exists..."
aws s3api head-bucket --bucket {settings.s3_terraform_state_bucket} 2>/dev/null || \\
  aws s3api create-bucket \\
    --bucket {settings.s3_terraform_state_bucket} \\
    --region {settings.s3_region} \\
    --create-bucket-configuration LocationConstraint={settings.s3_region}

# Enable versioning on state bucket
echo "🔄 Enabling versioning..."
aws s3api put-bucket-versioning \\
  --bucket {settings.s3_terraform_state_bucket} \\
  --versioning-configuration Status=Enabled

# Enable encryption
echo "🔐 Enabling encryption..."
aws s3api put-bucket-encryption \\
  --bucket {settings.s3_terraform_state_bucket} \\
  --server-side-encryption-configuration '{{
    "Rules": [{{
      "ApplyServerSideEncryptionByDefault": {{
        "SSEAlgorithm": "AES256"
      }}
    }}]
  }}'

# Create DynamoDB table for state locking (if doesn't exist)
echo "🔒 Creating DynamoDB lock table..."
aws dynamodb describe-table --table-name {settings.dynamodb_terraform_lock_table} 2>/dev/null || \\
  aws dynamodb create-table \\
    --table-name {settings.dynamodb_terraform_lock_table} \\
    --attribute-definitions AttributeName=LockID,AttributeType=S \\
    --key-schema AttributeName=LockID,KeyType=HASH \\
    --billing-mode PAY_PER_REQUEST \\
    --region {settings.s3_region}

# Initialize Terraform
echo "⚙️  Initializing Terraform..."
terraform init

echo "✅ Backend setup complete!"
echo ""
echo "Next steps:"
echo "  1. Review your .tf files"
echo "  2. Run: terraform plan"
echo "  3. Run: terraform apply"
echo ""
echo "Your state will be stored at:"
echo "  s3://{settings.s3_terraform_state_bucket}/states/{project_id}/terraform.tfstate"
'''


def generate_readme() -> str:
    """Generate README explaining Terraform state management."""
    
    return f'''# Terraform State Management

## Overview

Your Terraform state is stored in **S3 with versioning** and **DynamoDB locking** to prevent issues like:
- Creating duplicate resources (VPCs, etc.)
- Concurrent apply conflicts
- State corruption

## State Location

- **S3 Bucket**: `{settings.s3_terraform_state_bucket}`
- **DynamoDB Table**: `{settings.dynamodb_terraform_lock_table}`
- **Region**: `{settings.s3_region}`

## Setup Instructions

### 1. Run Backend Setup Script

```bash
chmod +x terraform-setup.sh
./terraform-setup.sh
```

This creates:
- S3 bucket with versioning + encryption
- DynamoDB table for locking
- Initializes Terraform backend

### 2. Deploy Infrastructure

```bash
# Review changes
terraform plan

# Apply infrastructure
terraform apply

# Destroy when done
terraform destroy
```

## State Versioning

Every `terraform apply` creates a **new version** in S3.

To view versions:
```bash
aws s3api list-object-versions \\
  --bucket {settings.s3_terraform_state_bucket} \\
  --prefix states/YOUR_PROJECT_ID/
```

To restore a previous version:
```bash
# Download specific version
aws s3api get-object \\
  --bucket {settings.s3_terraform_state_bucket} \\
  --key states/YOUR_PROJECT_ID/terraform.tfstate \\
  --version-id VERSION_ID \\
  terraform.tfstate

# Then run terraform apply with the restored state
```

## Troubleshooting

### Issue: "Error acquiring state lock"

**Cause**: Previous terraform command didn't finish cleanly.

**Solution**:
```bash
# Force unlock (use with caution!)
terraform force-unlock LOCK_ID
```

### Issue: "VPC limit reached"

**Cause**: Terraform doesn't know what's already created.

**Solution**: Ensure you're using the S3 backend (check `backend.tf`).

### Issue: State is out of sync

**Cause**: Manual changes in AWS Console.

**Solution**:
```bash
# Import existing resource
terraform import aws_vpc.main vpc-xxxxx

# Or refresh state
terraform refresh
```

## Best Practices

1. **Never edit state files manually**
2. **Always use state locking** (already configured)
3. **Keep recent versions** (auto-cleanup keeps last 10)
4. **Use workspaces for environments**: `terraform workspace new staging`
5. **Backup state before major changes**: Already handled by versioning

## Cost

- **S3 Storage**: ~$0.023/GB/month (state files are tiny)
- **DynamoDB**: Free tier covers locking operations
- **Total**: ~$0.10/month

## Security

- State files contain **sensitive data** (passwords, keys)
- S3 encryption is **enabled by default**
- Access requires **AWS credentials**
- Use **IAM roles** in production

## Further Reading

- [Terraform S3 Backend](https://www.terraform.io/docs/backends/types/s3.html)
- [State Locking](https://www.terraform.io/docs/state/locking.html)
- [AWS Best Practices](https://docs.aws.amazon.com/prescriptive-guidance/latest/terraform-aws-provider-best-practices/)
'''
