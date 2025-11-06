"""
Terraform output validator
"""

import re
from typing import Dict
import logging

from .validation_result import ValidationResult

logger = logging.getLogger(__name__)


class TerraformValidator:
    """Validates terraform output for common issues"""
    
    FORBIDDEN_TERMS = ['PLACEHOLDER', 'TODO', 'FIXME', 'XXX', 'CHANGEME', 'REPLACE_ME']
    REQUIRED_FILES = ['main.tf', 'variables.tf', 'outputs.tf', 'iam.tf']
    
    def validate(self, files: Dict[str, str]) -> ValidationResult:
        """
        Validate terraform files
        
        Args:
            files: Dict mapping filename to content
            
        Returns:
            ValidationResult with errors and warnings
        """
        errors = []
        warnings = []
        
        # Check for required files
        missing = [f for f in self.REQUIRED_FILES if f not in files]
        if missing:
            errors.append(f"Missing required files: {', '.join(missing)}")
        
        # Check for forbidden terms (placeholders, TODOs)
        for filename, content in files.items():
            for term in self.FORBIDDEN_TERMS:
                if term in content.upper():
                    # Find the line number
                    lines = content.split('\n')
                    for i, line in enumerate(lines, 1):
                        if term in line.upper():
                            errors.append(
                                f"Found forbidden term '{term}' in {filename}:{i}"
                            )
                            break
        
        # Check for undefined variables
        if 'variables.tf' in files:
            undefined_vars = self._check_undefined_variables(files)
            if undefined_vars:
                for filename, vars_list in undefined_vars.items():
                    errors.append(
                        f"{filename} references undefined variables: {', '.join(vars_list)}"
                    )
        
        # Check for hardcoded "myapp"
        for filename, content in files.items():
            if '"myapp"' in content or "'myapp'" in content:
                if 'default' not in content.lower():  # Allow in default values
                    warnings.append(
                        f"{filename} contains hardcoded 'myapp' - should use variable"
                    )
        
        # Check for basic terraform syntax
        for filename, content in files.items():
            if not filename.endswith('.tf'):
                continue
            
            # Check for basic terraform structure
            has_resource_or_var = (
                'resource ' in content or 
                'variable ' in content or 
                'output ' in content or
                'data ' in content or
                'provider ' in content or
                'terraform ' in content
            )
            
            if not has_resource_or_var and filename != 'backend.tf':
                warnings.append(
                    f"{filename} appears to have invalid Terraform syntax - "
                    f"no resource/variable/output declarations found"
                )
        
        is_valid = len(errors) == 0
        
        if is_valid:
            logger.info(f"✅ Terraform validation passed ({len(files)} files)")
        else:
            logger.error(f"❌ Terraform validation failed with {len(errors)} errors")
        
        if warnings:
            logger.warning(f"⚠️  Terraform validation has {len(warnings)} warnings")
        
        return ValidationResult(
            valid=is_valid,
            errors=errors,
            warnings=warnings
        )
    
    def _check_undefined_variables(self, files: Dict[str, str]) -> Dict[str, list]:
        """Check for references to undefined variables"""
        undefined = {}
        
        # Extract defined variables from variables.tf
        defined_vars = set()
        if 'variables.tf' in files:
            var_pattern = r'variable\s+"(\w+)"'
            defined_vars = set(re.findall(var_pattern, files['variables.tf']))
        
        # Check other files for variable references
        for filename, content in files.items():
            if filename == 'variables.tf':
                continue
            
            # Find all ${var.X} references
            var_refs = set(re.findall(r'\$\{var\.(\w+)\}', content))
            
            # Check which ones are undefined
            undefined_in_file = var_refs - defined_vars
            if undefined_in_file:
                undefined[filename] = list(undefined_in_file)
        
        return undefined
