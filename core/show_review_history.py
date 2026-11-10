"""Project-local, append-only evidence for reproducible editorial decisions."""
from datetime import datetime, timezone
import json,threading,uuid
LOCK=threading.RLock()

def record(project, action, before=None, after=None, *, method='human', evidence=None):
    event={'id':uuid.uuid4().hex,'at':datetime.now(timezone.utc).isoformat(),'action':action,'method':method,'before':before,'after':after,'evidence':evidence or {}}
    with LOCK:
        project.artifacts_dir.mkdir(parents=True,exist_ok=True)
        with (project.artifacts_dir/'show_decisions.jsonl').open('a',encoding='utf-8') as file:file.write(json.dumps(event,ensure_ascii=False)+'\n')
    return event

def read(project):
    path=project.artifacts_dir/'show_decisions.jsonl'
    with LOCK:return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.is_file() else []
