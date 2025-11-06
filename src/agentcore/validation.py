"""
Validation utilities for generated infrastructure files.
Checks for common issues like hardcoded values, secrets, etc.
"""

import re
import logging
from typing import List, Dict, Tuple

logger = logging.getLogger(__name__)


class InfrastructureValidator:
    """Validates generated infrastructure files for best practices."""

    # Patterns that indicate hardcoded secrets/sensitive data
    SECRET_PATTERNS = [
        r'(?i)(password|passwd|pwd)\s*[=:]\s*["\'][^"\']{3,}["\']',
        r'(?i)(api[_-]?key|apikey)\s*[=:]\s*["\'][^"\']{10,}["\']',
        r'(?i)(secret|token)\s*[=:]\s*["\'][^"\']{10,}["\']',
        r'(?i)(access[_-]?key|accesskey)\s*[=:]\s*["\'][A-Z0-9]{16,}["\']',
        r"AKIA[0-9A-Z]{16}",  # AWS Access Key ID pattern
        r'(?i)aws_secret_access_key\s*=\s*["\'][^"\']+["\']',
    ]

    # Patterns that should use variables instead of hardcoded values
    HARDCODE_PATTERNS = [
        (r"(?i)port\s*[=:]\s*(\d+)", "Port should use ARG/ENV variable"),
        (r"FROM\s+[\w/]+:latest", "Should use specific version tag instead of :latest"),
        (r'(?i)region\s*[=:]\s*["\']([a-z]{2}-[a-z]+-\d)["\']', "AWS region should be a variable"),
    ]

    def validate_dockerfile(self, content: str) -> Tuple[bool, List[str]]:
        """
        Validate Dockerfile for best practices.

        Returns:
            (is_valid, list_of_issues)
        """
        issues = []

        # Check for secrets
        for pattern in self.SECRET_PATTERNS:
            if re.search(pattern, content):
                issues.append(f"Potential hardcoded secret detected (pattern: {pattern[:30]}...)")

        # Check for hardcoded values
        for pattern, message in self.HARDCODE_PATTERNS:
            matches = re.findall(pattern, content, re.MULTILINE)
            if matches and pattern != r"FROM\s+[\w/]+:latest":  # Special handling for FROM
                issues.append(f"{message}: {matches[0] if matches else ''}")
            elif matches and ":latest" in content:
                issues.append(message)

        # Check for ARG/ENV usage
        has_args = bool(re.search(r"^ARG\s+\w+", content, re.MULTILINE))
        has_env = bool(re.search(r"^ENV\s+\w+", content, re.MULTILINE))

        if not has_args and not has_env:
            issues.append("Consider using ARG/ENV for configurable values")

        # Check for HEALTHCHECK
        if "HEALTHCHECK" not in content:
            issues.append("Missing HEALTHCHECK instruction")

        # Check for non-root user
        if "USER" not in content or content.count("USER root") > 0:
            issues.append("Consider running as non-root user for security")

        is_valid = len(issues) == 0

        if issues:
            logger.warning(f"Dockerfile validation found {len(issues)} issues")
        else:
            logger.info("Dockerfile validation passed")

        return is_valid, issues

    def validate_terraform(self, files: Dict[str, str]) -> Tuple[bool, List[str]]:
        """
        Validate Terraform files for best practices.

        Returns:
            (is_valid, list_of_issues)
        """
        issues = []
        all_content = "\n".join(files.values())

        # Check for secrets
        for pattern in self.SECRET_PATTERNS:
            if re.search(pattern, all_content):
                issues.append(f"Potential hardcoded secret in Terraform files")
                break

        # Check for variables usage
        has_variables = 'variable "' in all_content or "var." in all_content
        has_outputs = 'output "' in all_content

        if not has_variables:
            issues.append("No variables defined - consider using variables for configurable values")

        if not has_outputs:
            issues.append("No outputs defined - consider adding outputs for important resources")

        # Check for hardcoded regions
        if re.search(r'region\s*=\s*["\'][a-z]{2}-[a-z]+-\d["\']', all_content):
            issues.append("AWS region appears to be hardcoded - consider using variable")

        is_valid = len(issues) == 0

        if issues:
            logger.warning(f"Terraform validation found {len(issues)} issues")
        else:
            logger.info("Terraform validation passed")

        return is_valid, issues

    def validate_all(self, dockerfile: str, terraform_files: Dict[str, str]) -> Dict[str, any]:
        """
        Validate all generated files.

        Returns:
            Validation report with issues
        """
        dockerfile_valid, dockerfile_issues = self.validate_dockerfile(dockerfile)
        terraform_valid, terraform_issues = self.validate_terraform(terraform_files)

        report = {
            "overall_valid": dockerfile_valid and terraform_valid,
            "dockerfile": {"valid": dockerfile_valid, "issues": dockerfile_issues},
            "terraform": {"valid": terraform_valid, "issues": terraform_issues},
            "total_issues": len(dockerfile_issues) + len(terraform_issues),
        }

        logger.info(f"Validation complete: {report['total_issues']} total issues found")

        return report
