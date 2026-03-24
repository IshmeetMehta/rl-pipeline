# Helm Provider Configuration
provider "helm" {
  kubernetes {
    host                   = "https://${google_container_cluster.primary.endpoint}"
    token                  = data.google_client_config.default.access_token
    cluster_ca_certificate = base64decode(google_container_cluster.primary.master_auth[0].cluster_ca_certificate)
  }
}

# 1. Kuberay Helm Release (v1.5.1)
# ---------------------------------------------------------
resource "helm_release" "kuberay_operator" {
  name       = "kuberay-operator"
  repository = "https://ray-project.github.io/kuberay-helm/"
  chart      = "kuberay-operator"
  version    = "1.5.1"
  namespace  = "kube-system" # Installing into kube-system for operator scope
  
  create_namespace = true

  set {
    name  = "operator.image.repository"
    value = "kuberay/operator"
  }

  set {
    name  = "operator.image.tag"
    value = "v1.5.1"
  }
}
