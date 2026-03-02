import json

input_path = "train.jsonl"
output_path = "train_fixed.jsonl"

with open(input_path, "r") as f_in, open(output_path, "w") as f_out:
    for line in f_in:
        data = json.loads(line)
        # Add the required task_name
        data["task_name"] = "math"
        # Write back as a single line
        f_out.write(json.dumps(data) + "\n")

print(f"Fixed file saved to: {output_path}")
