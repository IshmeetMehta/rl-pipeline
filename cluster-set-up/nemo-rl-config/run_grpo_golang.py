import argparse
import os
import re
import time
import ray
import torch
import requests
from typing import Any, Dict, Optional, List, Tuple
from omegaconf import OmegaConf

# NeMo RL Core Imports
from nemo_rl.algorithms.grpo import MasterConfig, grpo_train, setup
from nemo_rl.algorithms.utils import get_tokenizer
from nemo_rl.data.datasets import AllTaskProcessedDataset, load_response_dataset
from nemo_rl.data.interfaces import DatumSpec, TaskDataSpec, LLMMessageLogType
from nemo_rl.data.processors import register_processor, TokenizerType
from nemo_rl.distributed.virtual_cluster import init_ray
from nemo_rl.environments.interfaces import EnvironmentInterface, EnvironmentReturn
from nemo_rl.models.generation import configure_generation_config
from nemo_rl.utils.config import load_config, parse_hydra_overrides
from nemo_rl.utils.logger import get_next_experiment_dir

# ==============================================================================
# 1. CUSTOM GOLANG DATA PROCESSOR
# ==============================================================================

def golang_processor(
    datum_dict: Dict[str, Any],
    task_data_spec: TaskDataSpec,
    tokenizer: TokenizerType,
    max_seq_length: int,
    idx: int,
) -> DatumSpec:
    problem = datum_dict["input"]
    extra_env_info = {"test_code": datum_dict.get("extra_env_info", {}).get("test_code", "")}
    message_log: LLMMessageLogType = []

    if task_data_spec.system_prompt:
        sys_formatted = tokenizer.apply_chat_template(
            [{"role": "system", "content": task_data_spec.system_prompt}],
            tokenize=False, add_generation_prompt=False, add_special_tokens=False,
        )
        message_log.append({
            "role": "system", "content": sys_formatted,
            "token_ids": tokenizer(sys_formatted, return_tensors="pt", add_special_tokens=False)["input_ids"][0]
        })

    user_content = task_data_spec.prompt.format(problem) if task_data_spec.prompt else problem
    user_formatted = tokenizer.apply_chat_template(
        [{"role": "user", "content": user_content}],
        tokenize=False, add_generation_prompt=True, add_special_tokens=False,
    )
    message_log.append({
        "role": "user", "content": user_formatted,
        "token_ids": tokenizer(user_formatted, return_tensors="pt", add_special_tokens=False)["input_ids"][0]
    })

    length = sum(len(m["token_ids"]) for m in message_log)
    return {
        "message_log": message_log,
        "length": length,
        "extra_env_info": extra_env_info,
        "loss_multiplier": 1.0 if length <= max_seq_length else 0.0,
        "idx": idx,
        "task_name": datum_dict.get("task_name", "go_verify_task")
    }

register_processor("golang_processor", golang_processor)

# ==============================================================================
# 2. CUSTOM GOLANG ENVIRONMENT
# ==============================================================================

@ray.remote(max_restarts=-1, max_task_retries=-1)
class GolangRemoteEnv(EnvironmentInterface):
    def __init__(self, config):
        self.base_url = config.get("base_urls")[0]
        self.session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_connections=100, pool_maxsize=100)
        self.session.mount('http://', adapter)

    def shutdown(self) -> None:
        self.session.close()

    async def step(self, message_log_batch, metadata) -> EnvironmentReturn:
        results, observations = [], []
        url = f"{self.base_url.rstrip('/')}/verify"
        
        for i, log in enumerate(message_log_batch):
            raw_response = "".join([m["content"] for m in log if m["role"] == "assistant"])
            test_info = metadata[i].get("extra_env_info", {})
            
            try:
                response = self.session.post(
                    url, 
                    json={"response": raw_response, "extra_env_info": test_info}, 
                    timeout=30
                )
                reward = float(response.json().get("reward", 0.0))
            except Exception as e:
                print(f"⚠️ Reward Server Error: {e}")
                reward = 0.0
            
            results.append(reward)
            label = "Environment: correct" if reward > 0.5 else "Environment: incorrect"
            observations.append({"role": "environment", "content": label})
            
        rewards = torch.tensor(results).cpu()
        return EnvironmentReturn(
            observations=observations, metadata=metadata, rewards=rewards,
            terminateds=torch.ones_like(rewards).cpu(),
            next_stop_strings=[None] * len(results),
            answers=[None] * len(results)
        )

    def global_post_process_and_metrics(self, batch):
        return batch, {"accuracy": batch["rewards"].mean().item()}

    def collect_rollout_metrics(self, message_log_batch, env_return):
        return {}

# ==============================================================================
# 3. MAIN RUNNER
# ==============================================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=str, required=True)
    args, overrides = parser.parse_known_args()

    config = load_config(args.config)
    if overrides:
        config = parse_hydra_overrides(config, [o for o in overrides if not o.startswith('-')])
    
    m_cfg: MasterConfig = OmegaConf.to_container(config, resolve=True)
    init_ray()
    
    m_cfg["logger"]["log_dir"] = get_next_experiment_dir(m_cfg["logger"]["log_dir"])
    tokenizer = get_tokenizer(m_cfg["policy"]["tokenizer"])
    
    gen_cfg = m_cfg["policy"]["generation"]
    for k in ["stop_token_ids", "stop_strings"]:
        gen_cfg[k] = gen_cfg.get(k, None)
    m_cfg["policy"]["generation"] = configure_generation_config(gen_cfg, tokenizer)

    # Allow NeMo to build task_spec and processor configs
    data_info = load_response_dataset(m_cfg["data"], m_cfg["grpo"]["seed"])
    
    # ==============================================================================
    # EXPLICIT DATA LOADING DIRECTLY FROM YAML CONFIG (PRODUCTION)
    # ==============================================================================
    from datasets import load_dataset
    
    train_path = m_cfg["data"]["train"][0]["data_path"]
    val_path = m_cfg["data"]["validation"][0]["data_path"]
    
    print(f"📥 Loading full Production Train data: {train_path}")
    print(f"📥 Loading Production Val data: {val_path}")

    # Load the raw files directly using Hugging Face
    raw_train = load_dataset("json", data_files=train_path, split="train")
    raw_val = load_dataset("json", data_files=val_path, split="train")

    # Build the final NeMo formatted datasets (FIXED ROUTING HERE)
    dataset = AllTaskProcessedDataset(
        raw_train, tokenizer, data_info.task_spec,
        {"go_verify_task": (data_info.task_spec, data_info.processor)}, 
        max_seq_length=m_cfg["data"]["max_input_seq_length"]
    )

    val_dataset = AllTaskProcessedDataset(
        raw_val, tokenizer, data_info.task_spec,
        {"go_verify_task": (data_info.task_spec, data_info.processor)}, 
        max_seq_length=m_cfg["data"]["max_input_seq_length"]
    )
    # ==============================================================================

    env = GolangRemoteEnv.remote(m_cfg["env"][m_cfg["data"]["env_name"]]) 
    task_to_env = {"go_verify_task": env} # <--- FIXED ROUTING HERE

    (policy, policy_gen, cluster, dl, val_dl, loss_fn, logger, ckpt, state, final_cfg) = setup(m_cfg, tokenizer, dataset, val_dataset)

    print("🚀 STARTING GRPO TRAINING")
    grpo_train(policy, policy_gen, dl, val_dl, tokenizer, loss_fn, task_to_env, task_to_env, logger, ckpt, state, final_cfg)

    # --- GUARANTEED SHUTDOWN HOOK FOR GCS FUSE ---
    print(f"\n✅ Training loop finished. Activating Ray cluster hold hook...")
    wait_time_seconds = 300 # 5 minutes
    
    for remaining in range(wait_time_seconds, 0, -10):
        print(f"⏳ Holding Ray workers alive... {remaining} seconds remaining for GCS background upload.")
        time.sleep(10)

    print("✅ GCS FUSE sync window complete!")
    # ray.shutdown()
    # print("🛑 Shutdown complete.")

if __name__ == "__main__":
    main()