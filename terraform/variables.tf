variable "project_id" {
  description = "GCP Project ID"
  type        = string
}

variable "region" {
  description = "GCP region"
  type        = string
  default     = "us-central1"
}

variable "app_name" {
  description = "Application name (must match Docker image name)"
  type        = string
}

variable "image_uri" {
  description = "Full Docker image URI from Artifact Registry"
  type        = string
}

variable "app_env_vars" {
  description = "Application environment variables"
  type        = map(string)
  default     = null
}
