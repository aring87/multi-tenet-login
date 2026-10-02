"""Read-only workflow history from local receipts and explicitly refreshed GitHub runs."""
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import stat

from .preview_workflow import PreviewService, WORKFLOW, WORKFLOW_PATH
from .repository_reviews import require, fingerprint, check_path
from .rule_drafts import atomic_write

MAX_FILE = 2 * 1024 * 1024
MAX_REQUESTS = 200
FOLDERS = {'preview': 'preview-requests', 'deploy': 'deployment-requests'}
SUFFIXES = {'preview': '.preview.json', 'deploy': '.deployment.json'}


def linked(path):
    info=Path(path).lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info,'st_file_attributes',0) & 0x400)


def validate(record, mode):
    require(isinstance(record,dict) and record.get('version')==1, 'Unsupported workflow receipt.')
    require(re.fullmatch(r'[0-9a-f]{32}',record.get('request_id','')), 'Invalid request ID.')
    plan=record['plan']
    require(isinstance(plan,dict) and record.get('fingerprint')==fingerprint(plan), 'Saved workflow review was changed.')
    require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+',plan['repository']), 'Invalid repository.')
    require(re.fullmatch(r'[0-9a-f]{40}',plan['revision']), 'Invalid reviewed revision.')
    require(plan['branch']=='main' and isinstance(plan['identity'],str), 'Invalid workflow review.')
    for key in ('repository_id','workflow_id'):
        require(type(plan[key]) is int and plan[key]>0, 'Invalid GitHub identity.')
    check_path(plan['rule_path'])
    targets=plan['targets']
    require(isinstance(targets,list) and 0<len(targets)<=100 and all(isinstance(t,str) and
        re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*',t) and len(t)<=64 for t in targets) and
        len(set(targets))==len(targets), 'Invalid saved targets.')
    batches=plan['batches']
    require(isinstance(batches,list) and 0<len(batches)<=100 and all(isinstance(b,dict) and
        b.get('mode')==mode and b.get('rule_path')==plan['rule_path'] for b in batches), 'Invalid workflow batches.')
    clients=plan['clients']
    require(isinstance(clients,list) and len(clients)==len(targets) and all(isinstance(c,dict) and
        all(isinstance(c.get(k),str) for k in ('name','target','workspace','subscription_id','resource_group','rule_state'))
        for c in clients) and sorted(c['target'] for c in clients)==sorted(targets), 'Invalid saved client list.')
    require(type(record.get('attempted')) is bool and isinstance(record.get('results'),list), 'Invalid submission receipt.')
    results=record['results']
    require(len(results)==(len(batches) if record['attempted'] else 0), 'Incomplete batch receipts.')
    seen=set()
    for batch,result in zip(batches,results):
        require(isinstance(result,dict) and result.get('inputs')==batch and isinstance(result.get('state'),str), 'Invalid batch result.')
        run=result.get('run_id')
        if run is not None:
            require(type(run) is int and run>0 and run not in seen, 'Invalid or duplicate run ID.')
            seen.add(run)
    require(isinstance(record.get('created_at'),str), 'Missing request time.')
    datetime.fromisoformat(record['created_at'])
    return record


def load_receipt(path):
    path=Path(path)
    mode=next((m for m,suffix in SUFFIXES.items() if path.name.endswith(suffix)),None)
    require(mode is not None, 'Choose a .preview.json or .deployment.json receipt.')
    require(not linked(path) and path.is_file(), 'Linked or non-file receipts are not supported.')
    with path.open('rb') as stream:raw=stream.read(MAX_FILE+1)
    require(len(raw)<=MAX_FILE,'Workflow receipt exceeds 2 MB.')
    return validate(json.loads(raw),mode),mode


def summary(record):
    results=record['results']
    if not record['attempted']:return 'Not submitted'
    if any(r.get('history_error') for r in results):return 'Refresh incomplete — previous status retained'
    if any(not r.get('run_id') for r in results):return 'Some batches unconfirmed / not submitted'
    if any(r.get('actual_revision') and r['actual_revision']!=record['plan']['revision'] for r in results):return 'Revision mismatch — inspect runs'
    if all(r.get('run_status')=='completed' and r.get('conclusion')=='success' for r in results):return 'All runs succeeded (last checked)'
    if any(r.get('run_status')=='completed' and r.get('conclusion') not in ('success',None) for r in results):return 'A run did not succeed (last checked)'
    if any(r.get('run_status') for r in results):return 'In progress / mixed (last checked)'
    return 'Submitted — status not checked'


def list_receipts(root):
    root=Path(root);candidates=[];issues=[]
    require(not root.exists() or not linked(root),'Linked history directories are not supported.')
    for mode,folder in FOLDERS.items():
        directory=root/folder
        if not directory.exists():continue
        if linked(directory):issues.append(folder+': linked folder skipped.');continue
        for path in directory.glob('*'+SUFFIXES[mode]):
            try:
                require(not linked(path) and path.is_file(),'Linked file')
                candidates.append((path.stat().st_mtime,path))
            except (OSError,ValueError,RuntimeError):issues.append(path.name+': unavailable or linked file.')
    candidates.sort(key=lambda x:(-x[0],str(x[1])))
    if len(candidates)>MAX_REQUESTS:issues.append('Showing the newest 200 receipts. Use Open receipt file for older requests.')
    rows=[]
    for _,path in candidates[:MAX_REQUESTS]:
        try:
            record,mode=load_receipt(path)
            rows.append(dict(file=path,mode=mode,record=record))
        except (OSError,ValueError,RuntimeError,KeyError,TypeError,AttributeError,RecursionError,OverflowError):
            issues.append(path.name+': invalid receipt; file left unchanged.')
    return rows,issues


def workflow_url(record, index=None):
    repository=record['plan']['repository']
    if index is not None and 0<=index<len(record['results']):
        run=record['results'][index].get('run_id')
        if run:return 'https://github.com/'+repository+'/actions/runs/'+str(run)
    return 'https://github.com/'+repository+'/actions/workflows/'+WORKFLOW


class WorkflowHistoryService:
    def __init__(self,request=None):self.reader=PreviewService(request)

    def refresh(self,path):
        # Re-read the file, never trust a stale table row or a saved URL.
        original,mode=load_receipt(path);record=copy.deepcopy(original)
        plan=record['plan'];prefix='repos/'+plan['repository']
        metadata=self.reader.request('GET',prefix)
        require(metadata.get('id')==plan['repository_id'] and metadata.get('private') is True,
                'Repository identity or visibility changed. Inspect the original request.')
        info=self.reader.request('GET',prefix+'/actions/workflows/'+str(plan['workflow_id']))
        require(info.get('id')==plan['workflow_id'] and info.get('path')==WORKFLOW_PATH,'Saved workflow does not match GitHub.')
        issues=[]
        for result in record['results']:
            if not result.get('run_id'):continue
            try:
                run=self.reader.read_run(plan,result['run_id'])
                require(isinstance(run.get('status'),str) and re.fullmatch(r'[0-9a-f]{40}',run.get('head_sha','')), 'Invalid run response.')
                result.update(run_status=run['status'],conclusion=run.get('conclusion'),actual_revision=run['head_sha'],
                              checked_at=datetime.now(timezone.utc).isoformat())
                result['state']=(run.get('conclusion') or 'completed') if run['status']=='completed' else run['status']
                if run['head_sha']!=plan['revision']:result['state']+=' — revision mismatch'
                result.pop('history_error',None)
            except (ValueError,RuntimeError,KeyError,TypeError) as error:
                result['history_error']='Latest status unavailable; previous status retained.'
                issues.append('Run '+str(result['run_id'])+': '+str(error))
        # Preserve the original request and submission information, including unknown
        # runs. No discovery by timing/title and no dispatch/retry endpoint exists here.
        require(load_receipt(path)[0]==original,'Receipt changed during refresh. Reload history before trying again.')
        atomic_write(Path(path),json.dumps(record,indent=2)+'\n')
        return record,mode,issues
