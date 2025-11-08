import json, click, subprocess, multiprocessing, time, uuid, os
from datetime import datetime, timezone

DATA_DIR = os.path.join(os.getcwd(), 'data')
JOBS_FILE = os.path.join(DATA_DIR, 'jobs.json')
CONFIG_FILE = os.path.join(DATA_DIR, 'config.json')

def ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def parse_iso(s):
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None

def load_data():
    ensure_data_dir()
    try:
        with open(JOBS_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        d = {"jobs": [], "dlq": []}
        with open(JOBS_FILE, 'w') as f: json.dump(d, f)
        return d

def save_data(d):
    ensure_data_dir()
    with open(JOBS_FILE, 'w') as f: json.dump(d, f, indent=2)

def load_config():
    ensure_data_dir()
    try:
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        c = {"max_retries":3,"backoff_base":2,"worker_idle_cycles":5}
        with open(CONFIG_FILE, 'w') as f: json.dump(c, f)
        return c

@click.group()
def cli():
    pass

@cli.command()
@click.argument('job_json')
def enqueue(job_json):
    d = load_data()
    j = json.loads(job_json)
    j["id"] = j.get("id", str(uuid.uuid4()))
    j["state"] = "pending"
    j["attempts"] = 0
    j["max_retries"] = j.get("max_retries", 3)
    j["timeout"] = j.get("timeout", None)
    j["priority"] = j.get("priority", 0)
    j["run_at"] = j.get("run_at", None)
    j["created_at"] = j["updated_at"] = now_iso()
    d["jobs"].append(j)
    save_data(d)
    click.echo(f'Job {j["id"]} enqueued')

@cli.command()
@click.argument('file_path')
def enqueue_file(file_path):
    d = load_data()
    with open(file_path, 'r') as f:
        j = json.load(f)
    j["id"] = j.get("id", str(uuid.uuid4()))
    j["state"] = "pending"
    j["attempts"] = 0
    j["max_retries"] = j.get("max_retries", 3)
    j["timeout"] = j.get("timeout", None)
    j["priority"] = j.get("priority", 0)
    j["run_at"] = j.get("run_at", None)
    j["created_at"] = j["updated_at"] = now_iso()
    d["jobs"].append(j)
    save_data(d)
    click.echo(f'Job {j["id"]} enqueued from {file_path}')

@cli.group()
def worker():
    pass

@worker.command("start")
@click.option('--count', default=1)
def start_worker(count):
    for _ in range(count):
        multiprocessing.Process(target=run_worker).start()
    click.echo(f'Started {count} worker(s)')

def runnable(job):
    ra = job.get("run_at")
    if not ra:
        return True
    parsed = parse_iso(ra)
    if not parsed:
        return True
    now = datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed <= now

def job_sort_key(job):
    return (-int(job.get("priority",0)), job.get("created_at",""))

def run_worker():
    cfg = load_config()
    idle_cycles = 0
    while True:
        d = load_data()
        pending = [j for j in d["jobs"] if j["state"] == "pending" and runnable(j)]
        pending.sort(key=job_sort_key)
        if not pending:
            idle_cycles += 1
            if idle_cycles > cfg.get("worker_idle_cycles",5):
                break
            time.sleep(2)
            continue
        idle_cycles = 0
        j = pending[0]
        j["state"] = "processing"
        j["updated_at"] = now_iso()
        save_data(d)
        timeout_val = None
        try:
            if j.get("timeout") is not None:
                try:
                    timeout_val = float(j.get("timeout"))
                except Exception:
                    timeout_val = None
            if timeout_val is not None:
                cp = subprocess.run(j["command"], shell=True, timeout=timeout_val)
                code = cp.returncode
            else:
                code = subprocess.call(j["command"], shell=True)
        except subprocess.TimeoutExpired:
            code = -1
        except Exception:
            code = -1
        cfg = load_config()
        d = load_data()
        for x in d["jobs"]:
            if x["id"] == j["id"]:
                if code == 0:
                    x["state"] = "completed"
                else:
                    x["attempts"] = x.get("attempts",0) + 1
                    if x["attempts"] <= x.get("max_retries", cfg.get("max_retries",3)):
                        delay = cfg.get("backoff_base",2) ** x["attempts"]
                        time.sleep(delay)
                        x["state"] = "pending"
                        x["updated_at"] = now_iso()
                    else:
                        x["state"] = "dead"
                        x["updated_at"] = now_iso()
                        d["dlq"].append(x)
                        d["jobs"] = [y for y in d["jobs"] if y["id"] != x["id"]]
                x["updated_at"] = now_iso()
        save_data(d)

@cli.command()
@click.option('--state', default=None)
def status(state):
    d = load_data()
    s = {}
    for j in d["jobs"]:
        if state and j["state"] != state:
            continue
        s[j["state"]] = s.get(j["state"],0)+1
    for k in sorted(s.keys()):
        click.echo(f'{k}: {s[k]}')
    if d["dlq"]:
        click.echo(f'dead: {len(d["dlq"])}')

@cli.group()
def dlq():
    pass

@dlq.command("list")
def dlq_list():
    d = load_data()
    if not d["dlq"]:
        click.echo("No DLQ jobs")
        return
    for j in d["dlq"]:
        click.echo(f'{j["id"]}: {j.get("command")} attempts={j.get("attempts",0)} priority={j.get("priority",0)} run_at={j.get("run_at")} timeout={j.get("timeout")}')

@dlq.command("retry")
@click.argument('job_id')
def dlq_retry(job_id):
    d = load_data()
    j = next((x for x in d["dlq"] if x["id"]==job_id), None)
    if not j:
        click.echo("Job not found in DLQ")
        return
    j["state"]="pending"
    j["attempts"]=0
    j["updated_at"]=now_iso()
    d["jobs"].append(j)
    d["dlq"] = [x for x in d["dlq"] if x["id"]!=job_id]
    save_data(d)
    click.echo(f'Job {job_id} requeued from DLQ')

@cli.group()
def config():
    pass

@config.command("set")
@click.argument('key')
@click.argument('value')
def config_set(key,value):
    c=load_config()
    try:v=int(value)
    except:
        try:v=float(value)
        except:
            v=value
    c[key]=v
    with open(CONFIG_FILE,'w') as f:json.dump(c,f,indent=2)
    click.echo(f'Set {key}={value}')

@config.command("show")
def config_show():
    c=load_config()
    for k,v in c.items():
        click.echo(f'{k}: {v}')

if __name__ == '__main__':
    cli()
import json, click, subprocess, multiprocessing, time, uuid, os
from datetime import datetime

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
JOBS_FILE = os.path.join(DATA_DIR, 'jobs.json')
CONFIG_FILE = os.path.join(DATA_DIR, 'config.json')

def ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)

def load_data():
    ensure_data_dir()
    try:
        with open(JOBS_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        d = {"jobs": [], "dlq": []}
        with open(JOBS_FILE, 'w') as f: json.dump(d, f)
        return d

def save_data(d):
    ensure_data_dir()
    with open(JOBS_FILE, 'w') as f: json.dump(d, f, indent=2)

def load_config():
    ensure_data_dir()
    try:
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        c = {"max_retries":3,"backoff_base":2}
        with open(CONFIG_FILE, 'w') as f: json.dump(c, f)
        return c

@click.group()
def cli():
    pass

@cli.command()
@click.argument('job_json')
def enqueue(job_json):
    d = load_data()
    j = json.loads(job_json)
    j["id"] = j.get("id", str(uuid.uuid4()))
    j["state"] = "pending"
    j["attempts"] = 0
    j["max_retries"] = j.get("max_retries", 3)
    j["created_at"] = j["updated_at"] = datetime.utcnow().isoformat()
    d["jobs"].append(j)
    save_data(d)
    click.echo(f'Job {j["id"]} enqueued')

@cli.command()
@click.argument('file_path')
def enqueue_file(file_path):
    d = load_data()
    with open(file_path, 'r') as f:
        j = json.load(f)
    j["id"] = j.get("id", str(uuid.uuid4()))
    j["state"] = "pending"
    j["attempts"] = 0
    j["max_retries"] = j.get("max_retries", 3)
    j["created_at"] = j["updated_at"] = datetime.utcnow().isoformat()
    d["jobs"].append(j)
    save_data(d)
    click.echo(f'Job {j["id"]} enqueued from {file_path}')

@cli.group()
def worker():
    pass

@worker.command("start")
@click.option('--count', default=1)
def start_worker(count):
    for _ in range(count):
        multiprocessing.Process(target=run_worker).start()
    click.echo(f'Started {count} worker(s)')

def run_worker():
    idle_cycles = 0
    while True:
        d = load_data()
        p = [j for j in d["jobs"] if j["state"] == "pending"]
        if not p:
            idle_cycles += 1
            if idle_cycles > 5:
                break
            time.sleep(2)
            continue
        idle_cycles = 0
        j = p[0]
        j["state"] = "processing"
        j["updated_at"] = datetime.utcnow().isoformat()
        save_data(d)
        code = subprocess.call(j["command"], shell=True)
        cfg = load_config()
        d = load_data()
        for x in d["jobs"]:
            if x["id"] == j["id"]:
                if code == 0:
                    x["state"] = "completed"
                else:
                    x["attempts"] += 1
                    if x["attempts"] <= x["max_retries"]:
                        delay = cfg.get("backoff_base",2) ** x["attempts"]
                        time.sleep(delay)
                        x["state"] = "pending"
                    else:
                        x["state"] = "dead"
                        d["dlq"].append(x)
                        d["jobs"] = [y for y in d["jobs"] if y["id"] != x["id"]]
                x["updated_at"] = datetime.utcnow().isoformat()
        save_data(d)

@cli.command()
def status():
    d = load_data()
    s = {}
    for j in d["jobs"]:
        s[j["state"]] = s.get(j["state"],0)+1
    for k,v in s.items():
        click.echo(f'{k}: {v}')
    if d["dlq"]:
        click.echo(f'dead: {len(d["dlq"]) }')

@cli.group()
def dlq():
    pass

@dlq.command("list")
def dlq_list():
    d = load_data()
    if not d["dlq"]:
        click.echo("No DLQ jobs")
        return
    for j in d["dlq"]:
        click.echo(f'{j["id"]}: {j["command"]} attempts={j["attempts"]}')

@dlq.command("retry")
@click.argument('job_id')
def dlq_retry(job_id):
    d = load_data()
    j = next((x for x in d["dlq"] if x["id"]==job_id), None)
    if not j:
        click.echo("Job not found in DLQ")
        return
    j["state"]="pending"
    j["attempts"]=0
    j["updated_at"]=datetime.utcnow().isoformat()
    d["jobs"].append(j)
    d["dlq"]=[x for x in d["dlq"] if x["id"]!=job_id]
    save_data(d)
    click.echo(f'Job {job_id} requeued from DLQ')

@cli.group()
def config():
    pass

@config.command("set")
@click.argument('key')
@click.argument('value')
def config_set(key,value):
    c=load_config()
    try:v=int(value)
    except:v=value
    c[key]=v
    with open(CONFIG_FILE,'w') as f:json.dump(c,f,indent=2)
    click.echo(f'Set {key}={value}')

@config.command("show")
def config_show():
    c=load_config()
    for k,v in c.items():
        click.echo(f'{k}: {v}')

if __name__ == '__main__':
    cli()
