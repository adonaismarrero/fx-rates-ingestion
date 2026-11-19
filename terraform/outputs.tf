output "image" {
  description = "Push the image here before the first scheduled run."
  value       = local.image
}

output "job_name" {
  value = google_cloud_run_v2_job.fx_ingest.name
}

output "fx_rates_table" {
  value = "${var.project_id}.${google_bigquery_dataset.raw.dataset_id}.fx_rates"
}
