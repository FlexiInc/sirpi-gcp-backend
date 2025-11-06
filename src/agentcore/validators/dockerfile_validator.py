"""
Dockerfile output validator
"""

import logging
from .validation_result import ValidationResult

logger = logging.getLogger(__name__)


class DockerfileValidator:
    """Validates Dockerfile output for best practices"""
    
    REQUIRED_INSTRUCTIONS = ['FROM', 'WORKDIR', 'COPY', 'CMD']
    FORBIDDEN_PATTERNS = ['PLACEHOLDER', 'TODO', 'FIXME', 'XXX']
    
    def validate(self, content: str) -> ValidationResult:
        """
        Validate Dockerfile content
        
        Args:
            content: Dockerfile content as string
            
        Returns:
            ValidationResult with errors and warnings
        """
        errors = []
        warnings = []
        
        # Check for required instructions
        for instruction in self.REQUIRED_INSTRUCTIONS:
            if instruction not in content:
                errors.append(f"Dockerfile missing required instruction: {instruction}")
        
        # Check for forbidden patterns
        for pattern in self.FORBIDDEN_PATTERNS:
            if pattern in content.upper():
                errors.append(f"Dockerfile contains placeholder: {pattern}")
        
        # Check for running as root
        if 'USER root' in content:
            warnings.append("Dockerfile should not run as root user for security")
        
        # Check for production best practices
        if 'USER ' not in content:
            warnings.append("Dockerfile should specify a non-root USER for security")
        
        if ':latest' in content:
            warnings.append("Using :latest tag is not recommended - specify version")
        
        if 'HEALTHCHECK' not in content:
            warnings.append("Consider adding HEALTHCHECK instruction for monitoring")
        
        # Check for multi-stage build (production best practice)
        if 'AS builder' not in content and 'as builder' not in content:
            warnings.append("Consider using multi-stage build for smaller image size")
        
        is_valid = len(errors) == 0
        
        if is_valid:
            logger.info("✅ Dockerfile validation passed")
        else:
            logger.error(f"❌ Dockerfile validation failed with {len(errors)} errors")
        
        if warnings:
            logger.warning(f"⚠️  Dockerfile validation has {len(warnings)} warnings")
        
        return ValidationResult(
            valid=is_valid,
            errors=errors,
            warnings=warnings
        )
