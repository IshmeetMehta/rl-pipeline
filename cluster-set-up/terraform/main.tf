# 1. Create the Main GKE Cluster
resource "google_container_cluster" "primary" {
  provider = google-beta

  name     = var.cluster_name
  location = var.zone

  # Initial size for the default node pool
  initial_node_count = 1

  # Support for Workload Identity
  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }

  # Addons: GCS Fuse and Ray Operator
  addons_config {
    gcs_fuse_csi_driver_config {
      enabled = true
    }
    ray_operator_config {
       enabled = false # Disabling managed addon to use manual Helm v1.5.1 installation
    }
  }

  # Enable logging and monitoring as requested
  logging_service    = "logging.googleapis.com/kubernetes"
  monitoring_service = "monitoring.googleapis.com/kubernetes"

  # We specify we're going to create separate node pools later
  # but per user instructions we specify the machine type for the base one too if we don't remove it.
  # However, it's better to explicitly manage the default pool machine type.
  node_config {
    machine_type = "n2-standard-16"
    
    # Required for GCS Fuse with workload identity
    workload_metadata_config {
      mode = "GKE_METADATA"
    }

    # Standard OAuth scopes
    oauth_scopes = [
      "https://www.googleapis.com/auth/cloud-platform"
    ]
  }

  # Keep cluster minimal for node-pool additions
  lifecycle {
    ignore_changes = [
      node_config,
    ]
  }
}

# 1.1 Artifact Registry for Reward Server
resource "google_artifact_registry_repository" "reward_repo" {
  location      = var.region
  repository_id = "reward-repo"
  description   = "Docker repository for Nemo RL Reward Server"
  format        = "DOCKER"
}

# 2. Ray Head Node Pool
resource "google_container_node_pool" "ray_head_pool" {
  name       = "ray-head-pool"
  cluster    = google_container_cluster.primary.id
  location   = var.zone
  node_count = 1

  node_config {
    machine_type = "n2-standard-32"
    disk_size_gb = 200
    disk_type    = "pd-ssd"
    
    labels = {
      "ray.io/node-type" = "head"
    }

    # Workload identity support
    workload_metadata_config {
      mode = "GKE_METADATA"
    }

    oauth_scopes = [
      "https://www.googleapis.com/auth/cloud-platform"
    ]
  }
}

# 3. GPU Worker Node Pool
resource "google_container_node_pool" "gpu_worker_pool" {
  name       = "gpu-multi-worker-pool"
  cluster    = google_container_cluster.primary.id
  location   = var.zone
  node_count = 2

  autoscaling {
    min_node_count = 0
    max_node_count = 4
  }

  node_config {
    machine_type = "g2-standard-24" # L4 compatible machine type
    disk_size_gb = 200
    
    # GPU Accelerator: 2x NVIDIA L4
    guest_accelerator {
      type  = "nvidia-l4"
      count = 2
      gpu_driver_installation_config {
        gpu_driver_version = "DEFAULT"
      }
    }

    metadata = {
      "install-nvidia-driver" = "true"
    }

    labels = {
      "ray.io/node-type" = "worker"
    }

    workload_metadata_config {
      mode = "GKE_METADATA"
    }

    oauth_scopes = [
      "https://www.googleapis.com/auth/cloud-platform"
    ]
  }
}

# 4. GVisor Sandbox Node Pool
resource "google_container_node_pool" "sandbox_pool" {
  name       = "sandbox"
  cluster    = google_container_cluster.primary.id
  location   = var.zone
  node_count = 1

  node_config {
    machine_type = "n2-standard-4"
    image_type   = "cos_containerd"

    # gVisor Sandbox configuration
    sandbox_config {
      sandbox_type = "gvisor"
    }

    workload_metadata_config {
      mode = "GKE_METADATA"
    }

    oauth_scopes = [
      "https://www.googleapis.com/auth/cloud-platform"
    ]
  }
}

# 5. HNS Bucket for RL Experiments
resource "google_storage_bucket" "experiment_bucket" {
  provider = google-beta
  name     = var.bucket_name
  location = var.region
  
  uniform_bucket_level_access = true
  
  hierarchical_namespace {
    enabled = true
  }
}

# 6. Workload Identity and GCS Permissions
# ---------------------------------------------------------

# Google Service Account (GSA)
resource "google_service_account" "nemo_gsa" {
  account_id   = "nemo-gsa"
  display_name = "Nemo RL GKE Service Account"
}

# IAM Binding for Storage
resource "google_storage_bucket_iam_member" "nemo_storage_admin" {
  bucket = google_storage_bucket.experiment_bucket.name
  role   = "roles/storage.admin"
  member = "serviceAccount:${google_service_account.nemo_gsa.email}"
}

# Workload Identity Binding
# Allow the KSA in 'default' namespace to impersonate the GSA
resource "google_service_account_iam_member" "workload_identity_user" {
  service_account_id = google_service_account.nemo_gsa.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[default/nemo-ksa]"
}

# Kubernetes Service Account (KSA) - Local placeholder for documentation
# and providing the annotation needed.
# Note: You can also use a 'kubernetes_service_account' resource if you have kubernetes provider configured.
output "ksa_annotation" {
  value = "iam.gke.io/gcp-service-account: ${google_service_account.nemo_gsa.email}"
}
