"""AWS deployment templates."""

from .fargate_template import fargate_template
from .lambda_template import lambda_template

__all__ = [
    "fargate_template",
    "lambda_template",
]
