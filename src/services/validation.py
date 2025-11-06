"""
Validation Service - Validates generated infrastructure files before PR creation.
"""

import logging
import re
from typing import Dict, List, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Result of validation check."""

    is_valid: bool
    errors: List[str]
    warnings: List[str]
    suggestions: List[str]


class InfrastructureValidator:
    """
    Validates generated Dockerfile and Terraform files for common issues.
    """

    def validate_dockerfile(self, content: str, framework: str = None) -> ValidationResult:
        """
        Validate Dockerfile for common issues and best practices.

        Args:
            content: Dockerfile content
            framework: Detected framework (next.js, react, etc.)

        Returns:
            ValidationResult with errors, warnings, and suggestions
        """
        errors = []
        warnings = []
        suggestions = []

        # Check if content is empty
        if not content or not content.strip():
            errors.append("Dockerfile is empty")
            return ValidationResult(
                is_valid=False, errors=errors, warnings=warnings, suggestions=suggestions
            )

        # Critical checks (errors)
        # Check first non-empty, non-comment line
        lines = [
            line.strip()
            for line in content.strip().split("\n")
            if line.strip() and not line.strip().startswith("#")
        ]
        if lines and not lines[0].startswith("FROM") and not lines[0].startswith("ARG"):
            errors.append(
                f"Dockerfile must start with FROM or ARG instruction (found: {lines[0][:50]})"
            )

        if "latest" in content.lower() and "FROM" in content:
            warnings.append("Using 'latest' tag is not recommended for production")

        # Check for hardcoded secrets
        secret_patterns = [
            r"(password|secret|key|token)\s*=\s*['\"][^'\"]+['\"]",
            r"AWS_ACCESS_KEY_ID\s*=",
            r"AWS_SECRET_ACCESS_KEY\s*=",
            r"GITHUB_TOKEN\s*=",
        ]
        for pattern in secret_patterns:
            if re.search(pattern, content, re.IGNORECASE):
                errors.append(f"Hardcoded secret detected: {pattern}")

        # Check for non-root user
        if "USER" not in content:
            warnings.append("No non-root user specified (security best practice)")

        # Check for HEALTHCHECK
        if "HEALTHCHECK" not in content:
            warnings.append("No HEALTHCHECK instruction found")

        # Check for multi-stage build
        from_count = content.count("FROM")
        if from_count == 1:
            suggestions.append("Consider using multi-stage build to reduce image size")

        # Framework-specific checks
        if framework:
            framework = framework.lower()
            if "next" in framework:
                if ".next/standalone" not in content:
                    warnings.append(
                        "Next.js: Missing .next/standalone - ensure output: 'standalone' in next.config"
                    )
                if "server.js" not in content:
                    warnings.append("Next.js: Missing server.js in CMD instruction")
                if "HOSTNAME" not in content:
                    warnings.append("Next.js: Missing HOSTNAME=0.0.0.0 environment variable")

        # Check for EXPOSE instruction
        if "EXPOSE" not in content:
            warnings.append("No EXPOSE instruction found")

        # Check for WORKDIR
        if "WORKDIR" not in content:
            warnings.append("No WORKDIR instruction found")

        is_valid = len(errors) == 0
        return ValidationResult(
            is_valid=is_valid, errors=errors, warnings=warnings, suggestions=suggestions
        )

    def validate_terraform(self, files: Dict[str, str]) -> ValidationResult:
        """
        Validate Terraform files for common issues.

        Args:
            files: Dictionary of filename -> content

        Returns:
            ValidationResult with errors, warnings, and suggestions
        """
        errors = []
        warnings = []
        suggestions = []

        # Check for required files
        required_files = ["main.tf", "variables.tf"]
        for required in required_files:
            if required not in files:
                warnings.append(f"Missing recommended file: {required}")

        # Check for hardcoded values in main.tf
        if "main.tf" in files:
            content = files["main.tf"]

            # Check for hardcoded AWS region
            if re.search(r'region\s*=\s*"[a-z]+-[a-z]+-\d+"', content):
                warnings.append("Hardcoded AWS region found - consider using variable")

            # Check for hardcoded account IDs
            if re.search(r"\d{12}", content):
                warnings.append("Hardcoded AWS account ID detected - use data source or variable")

            # Check for hardcoded IP addresses
            if re.search(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}", content):
                warnings.append("Hardcoded IP address found - consider using variable")

        # Check for backend configuration
        has_backend = any("backend" in content for content in files.values())
        if not has_backend:
            warnings.append("No backend configuration found - state will be stored locally")

        # Check for outputs
        has_outputs = "outputs.tf" in files or any(
            "output" in content for content in files.values()
        )
        if not has_outputs:
            suggestions.append("Consider adding outputs.tf for important resource information")

        is_valid = len(errors) == 0
        return ValidationResult(
            is_valid=is_valid, errors=errors, warnings=warnings, suggestions=suggestions
        )

    def validate_all(
        self, dockerfile: str, terraform_files: Dict[str, str], framework: str = None
    ) -> Tuple[ValidationResult, ValidationResult]:
        """
        Validate both Dockerfile and Terraform files.

        Returns:
            Tuple of (dockerfile_result, terraform_result)
        """
        dockerfile_result = self.validate_dockerfile(dockerfile, framework)
        terraform_result = self.validate_terraform(terraform_files)

        logger.info(
            f"Validation complete - Dockerfile: {dockerfile_result.is_valid}, "
            f"Terraform: {terraform_result.is_valid}"
        )

        return dockerfile_result, terraform_result


def get_validator() -> InfrastructureValidator:
    """Get validator instance."""
    return InfrastructureValidator()
