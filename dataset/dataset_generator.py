import json
import os
# pip install google-generativeai
import google.generativeai as genai 

# 1. Setup your API Key
genai.configure(api_key="YOUR_GEMINI_API_KEY")

# 2. Configuration
NUM_SAMPLES_NEEDED = 4000
OUTPUT_FILE = "golang_prompts_full.jsonl"

# We use gemini-1.5-flash because it is incredibly fast and cheap for data generation
model = genai.GenerativeModel('gemini-1.5-flash')

system_instruction = """
You are an expert Go programming dataset generator. 
Generate a unique, self-contained Go coding problem.
Return ONLY a valid JSON object with EXACTLY these two keys:
1. "input": A clear prompt asking to write a specific Go function.
2. "test_code": The complete, compilable Go code using the "testing" package that tests the requested function.

Example output:
{
  "input": "Write a Go function named `Multiply` that takes two integers and returns their product.",
  "test_code": "package main\nimport \"testing\"\nfunc TestMultiply(t *testing.T) {\n\tif Multiply(2, 3) != 6 {\n\t\tt.Fatal(\"Expected 6\")\n\t}\n}"
}
"""

print(f"🚀 Starting generation of {NUM_SAMPLES_NEEDED} Go problems...")

with open(OUTPUT_FILE, "a") as f:
    for i in range(NUM_SAMPLES_NEEDED):
        try:
            # Ask the LLM to generate a random Go problem
            response = model.generate_content(
                system_instruction + "\n\nGenerate a new, unique problem now. ONLY return JSON.",
                generation_config={"temperature": 0.9} # High temp for variety
            )
            
            # Clean up the response to get just the JSON
            raw_text = response.text.replace("```json", "").replace("```", "").strip()
            data = json.loads(raw_text)
            
            # Format it into your exact NeMo RLHF structure
            nemo_row = {
                "input": data["input"],
                "extra_env_info": {
                    "test_code": data["test_code"]
                },
                "task_name": "go_verify_task"
            }
            
            # Save to file immediately
            f.write(json.dumps(nemo_row) + "\n")
            print(f"✅ Generated sample {i+1}/{NUM_SAMPLES_NEEDED}")
            
        except Exception as e:
            print(f"⚠️ Failed to generate sample {i+1}, retrying... Error: {e}")

print(f"\n🎉 Finished! Upload {OUTPUT_FILE} to your GCS bucket.")