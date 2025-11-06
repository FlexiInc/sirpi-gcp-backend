terraform {
  required_version = ">= 1.9.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# Artifact Registry Repository - Reference existing shared registry
# Build step creates this repository if it doesn't exist
data "google_artifact_registry_repository" "main" {
  location      = var.region
  repository_id = "sirpi-deployments"
}

# Cloud Run Service
resource "google_cloud_run_v2_service" "main" {
  name     = var.app_name
  location = var.region

  template {
    containers {
      # Image name matches the project name set during build
      # Format: REGION-docker.pkg.dev/PROJECT_ID/REGISTRY/APP_NAME:TAG
      image = "${var.image_uri}"
      
      ports {
        container_port = 8000
      }
      
      # User-provided environment variables
      # Note: PORT is automatically set by Cloud Run to match container_port
      dynamic "env" {
        for_each = var.app_env_vars != null ? var.app_env_vars : {}
        content {
          name  = env.key
          value = env.value
        }
      }
      
      resources {
        limits = {
          cpu    = "1000m"
          memory = "512Mi"
        }
      }

      # Health check configuration
      startup_probe {
        http_get {
          path = "/api/v1/health"
          port = 8000
        }
        initial_delay_seconds = 30
        period_seconds        = 10
        timeout_seconds       = 5
        failure_threshold     = 5
      }
    }
    
    scaling {
      min_instance_count = 0
      max_instance_count = 10
    }
  }
  
  traffic {
    type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    percent = 100
  }
  
  lifecycle {
    ignore_changes = [
      template[0].containers[0].image,  # Allow image updates
    ]
  }
}

# IAM policy for public access
resource "google_cloud_run_v2_service_iam_member" "public" {
  location = google_cloud_run_v2_service.main.location
  name     = google_cloud_run_v2_service.main.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
