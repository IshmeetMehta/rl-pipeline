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
   region       = "us-east5"             # Chosen Region (e.g., us-east5 for Spot capacity)
   zone         = "us-east5-a"           # Chosen Zone
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
   gsutil -m cp -r ./qwen-gcs-upload/* gs://nemo-rl-experiments-pipeline/models/Qwen2.5-Coder-1.5B-Instruct/
   ```
3. **Upload RL Dataset**:
   ```bash
   gcloud storage cp dataset/golang_prompts.jsonl gs://nemo-rl-experiments-pipeline/datasets/golang_prompts.jsonl
   gcloud storage cp dataset/golang_val.jsonl gs://nemo-rl-experiments-pipeline/datasets/golang_val.jsonl
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
 
   gcloud storage cp cluster-set-up/nemo-rl-config/golang-env/run_grpo_golang.py gs://nemo-rl-experiments-pipeline/run_grpo_golang.py
   gcloud storage cp cluster-set-up/nemo-rl-config/golang-env/debug_grpo.yaml gs://nemo-rl-experiments-pipeline/configs/debug_grpo.yaml
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
Ray Dashboard: http://localhost:8265
   Port-forward the dashboard at `http://localhost:8265`:
   ```bash
   kubectl port-forward $HEAD_POD 8265:8265
   ```
Tensorboard: http://localhost:6006
   Port-forward the tensorboard at `http://localhost:6006`:
   ```bash
   kubectl port-forward $HEAD_POD 6006:6006
   ```

---

## Step 7: Serve Expert Model

After consolidation, deploy the fine-tuned expert via vLLM:
```bash
kubectl apply -f cluster-set-up/nemo-rl-config/golang-env/deployment/deployment.model.yaml
```

---

## 📊 Dataset Generation (AceCode to Go)

If you need to generate more training data, you can use the provided Jupyter notebook to transpile existing Python coding datasets (like AceCode) into Go.

### 1. Open the Notebook
Located at: `dataset/transpile_acecode_to_go.ipynb`

### 2. Configure Credentials
The notebook supports two authentication methods:
- **Vertex AI**: Set `use_gemini_api_key = False` and provide your `project_id`.
- **Gemini API Key**: Set `use_gemini_api_key = True` and paste your key from [Google AI Studio](https://makersuite.google.com/app/apikey).

### 3. Run the Transpiler
The notebook will:
1.  Load the `TIGER-Lab/AceCode-87K` dataset from Hugging Face.
2.  Use Gemini (specifically `gemini-2.5-flash`) to translate Python problems into Go functions and test harnesses.
3.  Format the output into the NeMo RLHF structure required for this pipeline:
    ```json
    {
      "input": "Write a Go function...",
      "extra_env_info": { "test_code": "package main..." },
      "task_name": "go_verify_task"
    }
    ```
4.  Save the results to `AceCode-87K-Go-Subset.jsonl`.

#### 💡 Critical Documentation for the Transpiler Logic:
- **Harness Requirements**: The generated `go_test_harness` MUST be a standalone `package main` with a `func main()`.
- **Failure Mode**: The harness must `panic` on error to signal a failed test to the Reward Server.
- **Isolation**: Do NOT include the solution function implementation inside the test harness; the Reward Server will automatically append the model's generated code to the harness before compilation.
- **Schema Validation**: The notebook uses **Pydantic** for strict JSON parsing to ensure all needed fields are present for RLHF training.

### 4. Upload to GCS
Once the `.jsonl` file is generated, upload it to your experiment bucket:
```bash
gcloud storage cp AceCode-87K-Go-Subset.jsonl gs://nemo-rl-experiments-pipeline/datasets/golang_prompts.jsonl
```
