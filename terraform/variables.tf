variable "project_id" {
  description = "GCP project that hosts the job and the dataset."
  type        = string
}

variable "region" {
  description = "Region for Cloud Run, Artifact Registry and Cloud Scheduler."
  type        = string
  default     = "europe-west1"
}

variable "bq_location" {
  description = "BigQuery dataset location."
  type        = string
  default     = "EU"
}

variable "dataset_id" {
  description = "Raw dataset that receives the fx_rates table."
  type        = string
  default     = "raw"
}

variable "image_tag" {
  description = "Tag of the fx-ingest image pushed to Artifact Registry."
  type        = string
  default     = "latest"
}

variable "base_currency" {
  type    = string
  default = "EUR"
}

variable "symbols" {
  description = "Comma-separated quote currencies."
  type        = string
  default     = "USD,GBP,CHF,JPY,MXN,BRL"
}

variable "schedule" {
  description = "Cron schedule. The ECB publishes around 16:00 CET on business days."
  type        = string
  default     = "30 17 * * 1-5"
}

variable "time_zone" {
  type    = string
  default = "Europe/Madrid"
}
