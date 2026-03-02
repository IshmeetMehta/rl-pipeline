import json

path = "train.jsonl"
with open(path, 'r') as f:
    for i, line in enumerate(f):
        try:
            data = json.loads(line)
            print(f"Row {i} validated: {data['input'][:30]}...")
        except Exception as e:
            print(f"Error in row {i}: {e}")
