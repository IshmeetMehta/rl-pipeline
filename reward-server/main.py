import os
import subprocess
import uuid
import logging
from fastapi import FastAPI, Body

# Setup logging to see requests in 'kubectl logs'
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("go-reward-server")

app = FastAPI()

def clean_code(text: str) -> str:
    """Strips Markdown backticks if the model wraps the code."""
    if "```go" in text:
        text = text.split("```go")[1].split("```")[0]
    elif "```" in text:
        text = text.split("```")[1].split("```")[0]
    return text.strip()

def run_command(command, cwd):
    try:
        result = subprocess.run(
            command, cwd=cwd, capture_output=True, text=True, timeout=10
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "Timeout"

@app.post("/verify")
async def verify_code(payload: dict = Body(...)):
    # 1. Capture the incoming NeMo payload
    # NeMo RL v0.5.0 sends 'response' and 'extra_env_info'
    raw_response = payload.get("response", "")
    extra_info = payload.get("extra_env_info", {})
    test_code = extra_info.get("test_code", "")
    
    logger.info(f"Received verification request. Response length: {len(raw_response)}")

    # 2. Clean the code (remove backticks)
    code = clean_code(raw_response)
    
    if not code:
        logger.warning("Empty code block after cleaning.")
        return {"reward": -1.0, "log": "Empty code"}

    # 3. Setup workspace
    job_id = str(uuid.uuid4())
    work_dir = f"/tmp/{job_id}"
    os.makedirs(work_dir, exist_ok=True)
    
    try:
        # Write Go files
        with open(f"{work_dir}/main.go", "w") as f:
            f.write(code)
        with open(f"{work_dir}/main_test.go", "w") as f:
            f.write(test_code)
        
        # Initialize Go module
        subprocess.run(["go", "mod", "init", f"reward/job_{job_id[:8]}"], 
                       cwd=work_dir, capture_output=True)

        # 4. Compile Check
        code_exit, _, stderr = run_command(["go", "build", "."], cwd=work_dir)
        if code_exit != 0:
            logger.info(f"Job {job_id}: Compile Error")
            return {"reward": -1.0, "log": f"Compile Error: {stderr}"}

        # 5. Run Unit Tests
        test_exit, stdout, stderr = run_command(["go", "test", "-v", "."], cwd=work_dir)
        if test_exit == 0:
            logger.info(f"Job {job_id}: Success (Reward 1.0)")
            return {"reward": 1.0, "log": "Success"}
        else:
            logger.info(f"Job {job_id}: Logic Error (Reward 0.2)")
            return {"reward": 0.2, "log": f"Logic Error: {stderr or stdout}"}

    except Exception as e:
        logger.error(f"Internal Server Error: {str(e)}")
        return {"reward": 0.0, "log": f"Internal Error: {str(e)}"}

    finally:
        # Cleanup
        subprocess.run(["rm", "-rf", work_dir])

if __name__ == "__main__":
    import uvicorn
    # uvicorn.run(app, host="0.0.0.0", port=8000)