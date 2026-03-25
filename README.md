# Nemo RL: Golang GRPO Pipeline

This project provides a complete environment and execution path to fine-tune `Qwen2.5-Coder` models into Golang experts using Reinforcement Learning (GRPO) on Ray and GKE.

## Project Structure

- `cluster-set-up/terraform/`: Managed infrastructure (GKE, Node Pools, HNS Bucket, Workload Identity).
- `reward-server/`: Go compilation and verification microservice (FastAPI + gVisor).
- `cluster-set-up/nemo-rl-config/golang-env/`: Training scripts and NemoRl configs.
- `dataset/`: Training data preparation.

---

## 🛠 Prerequisites: Configure your Environment

Before running any commands, you MUST create a `terraform.tfvars` file in the `cluster-set-up/terraform/` directory to specify your GCP project details.

1. **Create the file**:
   ```bash
   touch cluster-set-up/terraform/terraform.tfvars
   ```

2. **Add these variables**:
   ```hcl
   project_id   = "your-gcp-project-id"  # Your unique GCP Project ID
   region       = "us-west1"             # Chosen Region (G4s are common here)
   zone         = "us-west1-a"           # Chosen Zone
   cluster_name = "nemo-rl-ray"          # Desired cluster name
   bucket_name  = "your-unique-bucket"   # MUST be globally unique
   ```

---

## Step 1: Provision Infrastructure with Terraform
age & IAM (Terraform)

Terraform provisions the GKE cluster, the specialized node pools, the storage bucket, and the necessary **Workload Identity** (KSA/GSA) permissions for GCS access.

1. **Initialize Terraform**:
   ```bash
   cd cluster-set-up/terraform
   terraform init
   ```

2. **Configure Region & Project**:
   Update `terraform.tfvars` with your `project_id`, `region` (e.g., `us-central1`), and `zone` (e.g., `us-central1-a`). All infrastructure (GKE, GCS, Artifact Registry) will be provisioned in this location.

3. **Deploy Everything**:
   ```bash
   terraform apply
   ```
   **This creates:**
   - **GKE Cluster** with Head, GPU (RTX PRO 6000), and Sandbox (gVisor) pools.
   - **GCS Bucket** with Hierarchical Namespace (HNS) enabled.
   - **Artifact Registry Repository**: `reward-repo` for Docker images.
   - **GCP Service Account (GSA)**: `nemo-gsa` with Storage Admin roles.
   - **Kubernetes Service Account (KSA)**: `nemo-ksa` linked to the GSA via Workload Identity.
   - **KubeRay Operator**: Manual **v1.5.1** installation via Helm.

---

## Step 2: Stage Data and Model

Configure your GCS storage by uploading the RL dataset and the base model weights.

1. **Install HuggingFace CLI**:
   Ensure you have the CLI tools:
   ```bash
   pip install "huggingface_hub[cli]"
   ```

2. **Stage Base Model**:
   ```bash
   uvx hf download Qwen/Qwen2.5-Coder-1.5B-Instruct --local-dir ./qwen-gcs-upload
   gsutil -m cp -r ./qwen-gcs-upload/* gs://nemo-rl-experiments-rl-pipeline/models/Qwen2.5-Coder-1.5B-Instruct/
   ```
3. **Upload RL Dataset**:
   ```bash
   gcloud storage cp dataset/golang_prompts.jsonl gs://nemo-rl-experiments-rl-pipeline/datasets/golang_prompts.jsonl
   gcloud storage cp dataset/golang_val.jsonl gs://nemo-rl-experiments-rl-pipeline/datasets/golang_val.jsonl
   ```

---

## Step 3: Deploy the Auto-Healing Reward Server

To isolate the Go compilation environment and prevent RCE, we deploy a FastAPI service into the `gVisor` sandbox node pool.

1. **Build the Container Image**:
   Replace `{REGION}` with your chosen region (e.g., `us-central1`):
   ```bash
   cd reward-server
   gcloud builds submit . --config=cloudbuild.yaml --substitutions=_DESTINATION="{REGION}-docker.pkg.dev/$(gcloud config get-value project)/reward-repo/go-reward-server:v1"
   ```

2. **Deploy the Service**:
   Update the image in `reward-server/manifests/deployment.yaml` and apply:
   ```bash
   kubectl apply -f manifests/deployment.yaml
   ```

---

## Step 4: Deploy the Ray Cluster - Launching the Worker and Head Nodes

Deploy the Ray head and worker nodes using the optimized manifests for **NVIDIA RTX PRO 6000**.

1. **Apply Ray Cluster Manifests**:
   ```bash
   kubectl apply -f cluster-set-up/ray-config/ray-deployment/manifests/deployment.yaml
   ```
   *Note: These pods use the `nemo-ksa` service account to automatically mount the GCS bucket at `/mount/gcs` using the GCS Fuse CSI driver.*

---

## Step 5: Configure & Run Training

1. **Stage Training Files**:
   Copy the training runner and the nemoRl configuration files to the GCS bucket root (`/mount/gcs/`):
   ```bash
 
   gcloud storage cp cluster-set-up/nemo-rl-config/golang-env/run_grpo_golang.py gs://nemo-rl-experiments-rl-pipeline/run_grpo_golang.py
   gcloud storage cp cluster-set-up/nemo-rl-config/golang-env/debug_grpo.yaml gs://nemo-rl-experiments-rl-pipeline/configs/debug_grpo.yaml
   ```

2. **Execute on Ray Head**:
   ```bash
   export HEAD_POD=$(kubectl get pods -l ray.io/node-type=head -o name | head -n 1)

   kubectl exec -it $HEAD_POD -c ray-head -- bash -c " \
     PYTHONPATH=$PYTHONPATH:/mount/gcs/ \
     uv run python /mount/gcs/run_grpo_golang.py \
     --config /mount/gcs/configs/debug_grpo.yaml"
   ```

---

## Step 6: Monitoring & Evaluation

1. **Review Metrics**:
   Port-forward the dashboard at `http://localhost:8265`:
   ```bash
   kubectl port-forward $HEAD_POD 8265:8265
   ```

2. **Serve Expert Model**:
   After consolidation, deploy the fine-tuned expert via vLLM:
   ```bash
   kubectl apply -f cluster-set-up/nemo-rl-config/golang-env/deployment/deployment.model.yaml
   ```