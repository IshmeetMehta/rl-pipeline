# Kubernetes Provider Configuration
provider "kubernetes" {
  host                   = "https://${google_container_cluster.primary.endpoint}"
  token                  = data.google_client_config.default.access_token
  cluster_ca_certificate = base64decode(google_container_cluster.primary.master_auth[0].cluster_ca_certificate)
}

# Create the KSA and link it to the GSA
resource "kubernetes_service_account" "nemo_ksa" {
  metadata {
    name      = "nemo-ksa"
    namespace = "default"
    annotations = {
      "iam.gke.io/gcp-service-account" = google_service_account.nemo_gsa.email
    }
  }
  
  # Ensure the cluster and GSA are created first
  depends_on = [google_container_cluster.primary, google_service_account.nemo_gsa]
}

# Add IAM binding to the GSA for storage access (already done in main.tf)
