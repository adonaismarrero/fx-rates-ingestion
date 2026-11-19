locals {
  job_name = "fx-ingest"
  image    = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}/fx-ingest:${var.image_tag}"
}

resource "google_project_service" "apis" {
  for_each = toset([
    "artifactregistry.googleapis.com",
    "bigquery.googleapis.com",
    "cloudscheduler.googleapis.com",
    "run.googleapis.com",
  ])
  service            = each.value
  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "images" {
  repository_id = "ingestion"
  location      = var.region
  format        = "DOCKER"
  description   = "Container images for ingestion jobs."

  cleanup_policies {
    id     = "keep-last-10"
    action = "KEEP"
    most_recent_versions {
      keep_count = 10
    }
  }

  depends_on = [google_project_service.apis]
}

resource "google_bigquery_dataset" "raw" {
  dataset_id    = var.dataset_id
  location      = var.bq_location
  description   = "Raw data loaded by ingestion jobs."
  friendly_name = "raw"

  depends_on = [google_project_service.apis]
}

# --- Identities (least privilege) -------------------------------------------------------------

resource "google_service_account" "job" {
  account_id   = "fx-ingest-job"
  display_name = "fx-ingest Cloud Run job"
}

resource "google_service_account" "scheduler" {
  account_id   = "fx-ingest-scheduler"
  display_name = "Cloud Scheduler trigger for fx-ingest"
}

# The job runs queries (MERGE) in the project...
resource "google_project_iam_member" "job_bq_user" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.job.email}"
}

# ...but can only write to the raw dataset.
resource "google_bigquery_dataset_iam_member" "job_raw_editor" {
  dataset_id = google_bigquery_dataset.raw.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.job.email}"
}

# --- Job and schedule -------------------------------------------------------------------------

resource "google_cloud_run_v2_job" "fx_ingest" {
  name                = local.job_name
  location            = var.region
  deletion_protection = false

  template {
    task_count = 1
    template {
      service_account = google_service_account.job.email
      max_retries     = 1
      timeout         = "900s"

      containers {
        image = local.image
        env {
          name  = "FX_SINK"
          value = "bigquery"
        }
        env {
          name  = "FX_BQ_PROJECT"
          value = var.project_id
        }
        env {
          name  = "FX_BQ_DATASET"
          value = google_bigquery_dataset.raw.dataset_id
        }
        env {
          name  = "FX_BASE"
          value = var.base_currency
        }
        env {
          name  = "FX_SYMBOLS"
          value = var.symbols
        }
        resources {
          limits = {
            cpu    = "1"
            memory = "512Mi"
          }
        }
      }
    }
  }

  depends_on = [google_project_service.apis]
}

resource "google_cloud_run_v2_job_iam_member" "scheduler_invoker" {
  name     = google_cloud_run_v2_job.fx_ingest.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler.email}"
}

resource "google_cloud_scheduler_job" "daily" {
  name        = "${local.job_name}-daily"
  region      = var.region
  description = "Runs fx-ingest after the ECB publishes the daily reference rates."
  schedule    = var.schedule
  time_zone   = var.time_zone

  retry_config {
    retry_count = 2
  }

  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.project_id}/locations/${var.region}/jobs/${google_cloud_run_v2_job.fx_ingest.name}:run"

    oauth_token {
      service_account_email = google_service_account.scheduler.email
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }

  depends_on = [google_cloud_run_v2_job_iam_member.scheduler_invoker]
}
