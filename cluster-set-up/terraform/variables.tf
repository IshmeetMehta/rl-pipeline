variable "project_id" {
  description = "The GCP Project ID"
  type        = string
}

variable "region" {
  description = "The region for the GKE cluster"
  type        = string
  default     = "us-central1"
}

variable "zone" {
  description = "The zone for the GKE cluster and node pools"
  type        = string
  default     = "us-central1-a"
}

variable "cluster_name" {
  description = "The name of the GKE cluster"
  type        = string
  default     = "nemo-rl-ray"
}

variable "bucket_name" {
  description = "The name of the GCS bucket for experiments"
  type        = string
  default     = "nemo-rl-experiments-rl-pipeline" # Set to unique globally
}
