"""GCP deployment templates."""

from .cloud_run_template import cloud_run_template
from .gke_template import gke_template

__all__ = [
    "cloud_run_template",
    "gke_template",
]
