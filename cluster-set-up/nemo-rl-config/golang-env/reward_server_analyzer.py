import re
import argparse
from collections import Counter
from pathlib import Path

def analyze_go_logs(log_file_path):
    """
    Enhanced production-grade parser for Go Reward Server logs.
    Tracks rewards, failure reasons, payload sizes, execution timing metrics,
    and Auto-Healer rescues to help debug NeMo RL training performance.
    """
    stats = {
        "success": 0,
        "logic_fail": 0,
        "compile_fail": 0,
        "timeout": 0,
        "format_error": 0
    }
    
    times = []
    reasons = []
    payload_lengths = []
    
    # --- NEW: Track Auto-Healer Edge Cases ---
    auto_healed_jobs = set()
    healed_and_succeeded = 0
    
    log_path = Path(log_file_path)
    if not log_path.exists():
        print(f"Error: Log file '{log_file_path}' not found.")
        return

    print(f"--- Analyzing Reward Server Logs: {log_path.name} ---")
    
    # Regex Patterns
    request_pattern = r"\[JOB (?P<job_id>\w+)\] Received verification request\. Raw payload length: (?P<length>\d+)"
    reward_pattern = r"\[JOB (?P<job_id>\w+)\] REWARD: (?P<reward>[-\d.]+) \| REASON: (?P<reason>[^|]+)(?:\| TIME: (?P<time>[\d.]+)s)?"
    heal_pattern = r"\[JOB (?P<job_id>\w+)\] Auto-healing imports:"
    
    try:
        with open(log_file_path, 'r') as f:
            for line in f:
                # 1. Track Payload Lengths
                req_match = re.search(request_pattern, line)
                if req_match:
                    payload_lengths.append(int(req_match.group("length")))

                # 2. Track Auto-Healer Interventions
                heal_match = re.search(heal_pattern, line)
                if heal_match:
                    auto_healed_jobs.add(heal_match.group("job_id"))

                # 3. Track Rewards and Durations
                rew_match = re.search(reward_pattern, line)
                if rew_match:
                    job_id = rew_match.group("job_id")
                    reward = float(rew_match.group("reward"))
                    reason = rew_match.group("reason").strip()
                    duration = rew_match.group("time")
                    
                    if duration:
                        times.append(float(duration))
                    
                    reasons.append(reason)
                    
                    if reward == 1.0:
                        stats["success"] += 1
                        # Check if this success was rescued by the Auto-Healer!
                        if job_id in auto_healed_jobs:
                            healed_and_succeeded += 1
                    elif reward == 0.3:
                        stats["logic_fail"] += 1
                    elif reward == 0.1:
                        stats["compile_fail"] += 1
                    elif reward == 0.0:
                        stats["timeout"] += 1
                    elif reward == -1.0:
                        stats["format_error"] += 1

        total = sum(stats.values())
        if total == 0:
            print("No structured reward logs found. Ensure your server output is redirected to this file.")
            return

        # 1. Overall Statistics
        print(f"\n[Summary Metrics]")
        print(f"Total Requests: {total}")
        print(f"{'Status':<15} | {'Count':<10} | {'Percentage':<10}")
        print("-" * 40)
        for key, value in stats.items():
            percentage = (value / total) * 100
            print(f"{key.capitalize():<15} | {value:<10} | {percentage:>8.1f}%")

        # --- NEW: Display Auto-Healer Stats ---
        if auto_healed_jobs:
            print(f"\n[Auto-Healer Metrics (Import Edge Cases)]")
            print(f" - Total Interventions: {len(auto_healed_jobs)} jobs required import fixing.")
            print(f" - Successful Rescues:  {healed_and_succeeded} jobs (Rescued from 0.1 to 1.0!)")
            if len(auto_healed_jobs) > 0:
                rescue_rate = (healed_and_succeeded / len(auto_healed_jobs)) * 100
                print(f" - Rescue Rate:         {rescue_rate:.1f}%")

        # 2. Timing and Throughput
        if times:
            avg_time = sum(times) / len(times)
            max_time = max(times)
            p90_time = sorted(times)[int(len(times) * 0.9)] if len(times) > 10 else max_time
            
            print(f"\n[Performance Metrics]")
            print(f" - Average Latency: {avg_time:.2f}s")
            print(f" - P90 Latency:     {p90_time:.2f}s")
            print(f" - Max Latency:     {max_time:.2f}s")
            
            # Watchdog Analysis
            slow_jobs = len([t for t in times if t > 9.9])
            if slow_jobs > 0:
                print(f" - WARNING: {slow_jobs} jobs hit the 10s watchdog threshold.")

        # 3. Payload Size Trend Analysis
        if payload_lengths:
            avg_len = sum(payload_lengths) / len(payload_lengths)
            print(f"\n[Size Trend Analysis]")
            print(f" - Global Avg Size:  {avg_len:.1f} characters")
            
            if len(payload_lengths) > 20:
                chunk_size = max(1, len(payload_lengths) // 10)
                start_avg = sum(payload_lengths[:chunk_size]) / chunk_size
                end_avg = sum(payload_lengths[-chunk_size:]) / chunk_size
                diff = end_avg - start_avg
                trend = "INCREASING" if diff > 0 else "DECREASING"
                
                print(f" - Initial Avg Size: {start_avg:.1f} (First 10%)")
                print(f" - Final Avg Size:   {end_avg:.1f} (Last 10%)")
                print(f" - Net Change:       {diff:+.1f} ({trend})")
                
                if abs(diff) > (avg_len * 0.2):
                    print(f" - ALERT: Significant size shift detected (>20%). Check for reward hacking or verbosity drift.")

        # 4. Root Cause Analysis
        if reasons:
            print(f"\n[Top Failure Reasons]")
            for reason, count in Counter(reasons).most_common(5):
                print(f" - [{count:3}x]: {reason}")

    except Exception as e:
        print(f"Error processing logs: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Analyze Go Reward Server logs for RL training.")
    parser.add_argument("logfile", nargs="?", default="server.log", help="Path to the server log file (default: server.log)")
    args = parser.parse_args()
    
    analyze_go_logs(args.logfile)