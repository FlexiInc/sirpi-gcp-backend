"""
Validation result model
"""

from typing import List
from pydantic import BaseModel


class ValidationResult(BaseModel):
    """Result of validation"""
    valid: bool
    errors: List[str] = []
    warnings: List[str] = []
    
    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0
    
    @property
    def has_warnings(self) -> bool:
        return len(self.warnings) > 0
    
    def format_errors(self) -> str:
        """Format errors as readable string"""
        if not self.errors:
            return "No errors"
        return "\n".join(f"❌ {error}" for error in self.errors)
    
    def format_warnings(self) -> str:
        """Format warnings as readable string"""
        if not self.warnings:
            return "No warnings"
        return "\n".join(f"⚠️  {warning}" for warning in self.warnings)
