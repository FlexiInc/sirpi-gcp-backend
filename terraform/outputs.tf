output "service_url" {
  description = "Cloud Run service URL"
  value       = google_cloud_run_v2_service.main.uri
}

output "artifact_registry" {
  description = "Artifact Registry repository"
  value       = data.google_artifact_registry_repository.main.name
}

output "service_name" {
  description = "Cloud Run service name"
  value       = google_cloud_run_v2_service.main.name
}

output "location" {
  description = "Deployment location"
  value       = google_cloud_run_v2_service.main.location
}
