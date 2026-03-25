# 0. Enable Required GCP APIs
#B200 region  asia-east1-a
resource "google_project_service" "services" {
  for_each = toset([
    "container.googleapis.com",
    "compute.googleapis.com",
    "iam.googleapis.com",
    "artifactregistry.googleapis.com",
    "storage.googleapis.com",
    "cloudbuild.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
  ])

  project = var.project_id
  service = each.key

  disable_on_destroy = false
}

# 0.1 Create Main VPC Network and Subnet
resource "google_compute_network" "vpc_network" {
  name                    = "nemo-rl-vpc"
  auto_create_subnetworks = false
  depends_on              = [google_project_service.services]
}

resource "google_compute_subnetwork" "subnet" {
  name          = "nemo-rl-subnet"
  ip_cidr_range = "10.0.0.0/16"
  region        = var.region
  network       = google_compute_network.vpc_network.id

  secondary_ip_range {
    range_name    = "gke-pods-range"
    ip_cidr_range = "10.1.0.0/16"
  }

  secondary_ip_range {
    range_name    = "gke-services-range"
    ip_cidr_range = "10.2.0.0/16"
  }
}

# 0.2 Cloud Router and NAT (for Private Nodes to reach the internet)
resource "google_compute_router" "router" {
  name    = "nemo-rl-router"
  region  = var.region
  network = google_compute_network.vpc_network.id
}

resource "google_compute_router_nat" "nat" {
  name                               = "nemo-rl-nat"
  router                             = google_compute_router.router.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"
}

# 0.3 Create 1 Dedicated RoCE VPC and 8 Subnets for Blackwell GPUs
resource "google_compute_network" "rdma_vpc" {
  provider                = google-beta
  name                    = "nemo-rdma-vpc"
  auto_create_subnetworks = false
  mtu                     = 8896 # Required jumbo frames for maximum RDMA throughput
  
  # Assign the hardware RoCE network profile required by A4 VMs
  network_profile         = "projects/${var.project_id}/global/networkProfiles/${var.zone}-vpc-roce"
  
  depends_on              = [google_project_service.services]
}

resource "google_compute_subnetwork" "rdma_subnet" {
  provider      = google-beta
  count         = 8
  name          = "nemo-rdma-sub-${count.index}"
  ip_cidr_range = "192.168.${count.index}.0/24" # Non-overlapping ranges
  region        = var.region
  network       = google_compute_network.rdma_vpc.id
}

# 1. Create the Main GKE Cluster
resource "google_container_cluster" "primary" {
  provider = google-beta

  name     = var.cluster_name
  location = var.zone

  # Disable deletion protection for easy recreation
  deletion_protection = false

  network    = google_compute_network.vpc_network.name
  subnetwork = google_compute_subnetwork.subnet.name

  # Enable IP aliasing
  ip_allocation_policy {
    cluster_secondary_range_name  = "gke-pods-range"
    services_secondary_range_name = "gke-services-range"
  }

  # Private Cluster Configuration (Compliance: No External IPs)
  private_cluster_config {
    enable_private_nodes    = true   
    enable_private_endpoint = false  
    master_ipv4_cidr_block  = "172.16.0.0/28"
  }

  # Enable Advanced datapath (Required for Multi-networking)
  datapath_provider = "ADVANCED_DATAPATH"
  
  # Enable Multi-Networking for GPU RDMA support
  enable_multi_networking = true

  # Compliance: Shielded Nodes
  enable_shielded_nodes = true

  # Ensure services and networking are enabled before cluster creation
  depends_on = [google_project_service.services, google_compute_subnetwork.subnet]

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
       enabled = false # Manual Helm v1.5.1 installation preferred
    }
  }

  # Enable logging and monitoring
  logging_service    = "logging.googleapis.com/kubernetes"
  monitoring_service = "monitoring.googleapis.com/kubernetes"

  node_config {
    machine_type = "n2-standard-16"
    
    workload_metadata_config {
      mode = "GKE_METADATA"
    }

    shielded_instance_config {
      enable_secure_boot = true
    }

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
  
  depends_on = [google_project_service.services]
}

# 2. Ray Head Node Pool
resource "google_container_node_pool" "ray_head_pool" {
  provider   = google-beta
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

    workload_metadata_config {
      mode = "GKE_METADATA"
    }

    shielded_instance_config {
      enable_secure_boot = true
    }

    oauth_scopes = [
      "https://www.googleapis.com/auth/cloud-platform"
    ]
  }
}

# 3. GPU Worker Node Pool (Blackwell B200)
resource "google_container_node_pool" "gpu_worker_pool" {
  provider   = google-beta
  name       = "gpu-multi-worker-pool"
  cluster    = google_container_cluster.primary.id
  location   = var.zone
  node_count = 1 

  autoscaling {
    min_node_count = 0
    max_node_count = 2
  }

  depends_on = [
    google_compute_network.rdma_vpc,
    google_compute_subnetwork.rdma_subnet
  ]

  # BIND THE RDMA NETWORKS: Map the 8 subnets from the RoCE VPC
  network_config {
    enable_private_nodes = true

    dynamic "additional_node_network_configs" {
      for_each = google_compute_subnetwork.rdma_subnet
      content {
        network    = google_compute_network.rdma_vpc.name
        subnetwork = additional_node_network_configs.value.name
      }
    }
  }

  node_config {
    machine_type = "a4-highgpu-8g" 
    
    # GPU Accelerator: Blackwell B200 (8x per node)
    guest_accelerator {
      type  = "nvidia-b200"
      count = 8
      gpu_driver_installation_config {
        gpu_driver_version = "LATEST"
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

    shielded_instance_config {
      enable_secure_boot = true
    }

    oauth_scopes = [
      "https://www.googleapis.com/auth/cloud-platform"
    ]
  }
}

# 4. GVisor Sandbox Node Pool
resource "google_container_node_pool" "sandbox_pool" {
  provider   = google-beta
  name       = "sandbox"
  cluster    = google_container_cluster.primary.id
  location   = var.zone
  node_count = 1

  node_config {
    machine_type = "n2-standard-4"
    image_type   = "cos_containerd"

    sandbox_config {
      sandbox_type = "gvisor"
    }

    workload_metadata_config {
      mode = "GKE_METADATA"
    }

    shielded_instance_config {
      enable_secure_boot = true
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

  # Allow Terraform to delete bucket content on destroy
  force_destroy = true

  depends_on = [google_project_service.services]
}

# 6. Workload Identity and GCS Permissions
resource "google_service_account" "nemo_gsa" {
  account_id   = "nemo-gsa"
  display_name = "Nemo RL GKE Service Account"
  depends_on   = [google_project_service.services]
}

resource "google_storage_bucket_iam_member" "nemo_storage_admin" {
  bucket = google_storage_bucket.experiment_bucket.name
  role   = "roles/storage.admin"
  member = "serviceAccount:${google_service_account.nemo_gsa.email}"
}

resource "google_service_account_iam_member" "workload_identity_user" {
  service_account_id = google_service_account.nemo_gsa.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${google_container_cluster.primary.workload_identity_config[0].workload_pool}[default/nemo-ksa]"
  
  # Ensure the cluster is fully ready before binding Workload Identity
  depends_on = [google_container_cluster.primary]
}

output "ksa_annotation" {
  value = "iam.gke.io/gcp-service-account: ${google_service_account.nemo_gsa.email}"
}