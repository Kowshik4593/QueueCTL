import subprocess, os, json, time, shutil

def run(cmd):
    print(f"\n> {cmd}")
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if r.stdout: print(r.stdout.strip())
    if r.stderr and "DeprecationWarning" not in r.stderr: print(r.stderr.strip())

def reset():
    d = os.path.join(os.path.dirname(__file__), "data")
    if os.path.exists(d): shutil.rmtree(d)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "jobs.json"), "w") as f: json.dump({"jobs": [], "dlq": []}, f)
    with open(os.path.join(d, "config.json"), "w") as f: json.dump({"max_retries": 3, "backoff_base": 2}, f)

def test_basic():
    print("\n=== TEST 1: Basic Success ===")
    with open("job.json", "w") as f: json.dump({"command": "echo Hello World"}, f)
    run("python main.py enqueue-file job.json")
    run("python main.py status")
    run("python main.py worker start --count 1")
    time.sleep(2)
    run("python main.py status")

def test_invalid():
    print("\n=== TEST 2: Invalid Command + DLQ ===")
    with open("job.json", "w") as f: json.dump({"command": "unknown_command"}, f)
    run("python main.py enqueue-file job.json")
    run("python main.py worker start --count 1")
    time.sleep(12)
    run("python main.py dlq list")

def test_mixed():
    print("\n=== TEST 3: Mixed Valid + Invalid ===")
    with open("job1.json", "w") as f: json.dump({"command": "echo Success"}, f)
    with open("job2.json", "w") as f: json.dump({"command": "bad_command"}, f)
    run("python main.py enqueue-file job1.json")
    run("python main.py enqueue-file job2.json")
    run("python main.py worker start --count 2")
    time.sleep(10)
    run("python main.py status")
    run("python main.py dlq list")

def test_config():
    print("\n=== TEST 4: Config Change ===")
    run("python main.py config set max_retries 5")
    run("python main.py config set backoff_base 3")
    run("python main.py config show")

def test_persistence():
    print("\n=== TEST 5: Persistence ===")
    run("python main.py status")
    run("python main.py status")

def test_parallel():
    print("\n=== TEST 6: Parallel Workers ===")
    with open("job.json", "w") as f: json.dump({"command": "echo Parallel Job"}, f)
    for _ in range(3): run("python main.py enqueue-file job.json")
    run("python main.py worker start --count 3")
    time.sleep(3)
    run("python main.py status")

def test_corruption():
    print("\n=== TEST 7: Corrupted File Recovery ===")
    with open("data/jobs.json", "w") as f: f.write("")
    run("python main.py status")

def test_dlq_retry():
    print("\n=== TEST 8: DLQ Retry ===")
    run("python main.py dlq list")
    data = json.load(open("data/jobs.json"))
    if data["dlq"]:
        job_id = data["dlq"][0]["id"]
        run(f"python main.py dlq retry {job_id}")
        run("python main.py status")

def test_duplicate():
    print("\n=== TEST 9: Duplicate Job IDs ===")
    with open("dup.json", "w") as f: json.dump({"id": "dup", "command": "echo one"}, f)
    run("python main.py enqueue-file dup.json")
    run("python main.py enqueue-file dup.json")
    run("python main.py status")

def test_backoff_extreme():
    print("\n=== TEST 10: Extreme Backoff Config ===")
    run("python main.py config set max_retries 2")
    run("python main.py config set backoff_base 4")
    with open("job.json", "w") as f: json.dump({"command": "badcmd"}, f)
    run("python main.py enqueue-file job.json")
    run("python main.py worker start --count 1")
    time.sleep(5)
    run("python main.py dlq list")

if __name__ == "__main__":
    reset()
    test_basic()
    test_invalid()
    test_mixed()
    test_config()
    test_persistence()
    test_parallel()
    test_corruption()
    test_dlq_retry()
    test_duplicate()
    test_backoff_extreme()
    print("\n=== ALL TESTS EXECUTED ===")

