"""Read-only Azure access probes and local, per-subscription setup timestamps."""
import json
from pathlib import Path
import tempfile
import time
from datetime import datetime

from .auth_recovery import AuthenticationRequired
from .desktop_backend import Stop, guid, require
from .lighthouse_onboarding import EMPTY_TEMPLATE, AUTHORIZATION_FAILED
from .rule_drafts import atomic_write
from .workspace_tools import permits, subscription_permissions, SETUP_ACTIONS


def check_access(session, target, location):
    account = session.verify(target)
    require(session.az('cloud', 'show').get('name')=='AzureCloud', 'This access check supports Azure public cloud only.')
    blocks = subscription_permissions(session, target)
    missing = [a for a in SETUP_ACTIONS if not permits(blocks, a)]
    # Role listings and the permissions endpoint alone are not proof that ARM
    # accepts this session. Validate an empty template; never deploy or grant.
    with tempfile.TemporaryDirectory(prefix='sentinel-access-') as folder:
        path = Path(folder)/'probe.json'
        path.write_text(json.dumps(EMPTY_TEMPLATE), encoding='utf-8')
        try:
            session.az('deployment', 'sub', 'validate', '--subscription', target['subscription_id'],
                       '--location', location, '--name', 'lighthouse-access-check', '--template-file', str(path))
        except AuthenticationRequired:
            raise
        except Stop as error:
            if not AUTHORIZATION_FAILED.search(str(error)): raise
            return dict(ready=False, account=account['user']['name'], missing=missing,
                        detail='Azure still denies subscription deployment validation. '+str(error))
    return dict(ready=not missing, account=account['user']['name'], missing=missing,
                detail='Deployment validation passed.' if not missing else
                       'Deployment validation passed, but required setup actions are not all listed as permitted.')


class AccessHistory:
    def __init__(self, path): self.path=Path(path)

    def key(self, target):
        return guid(target['tenant_id'], 'tenant')+'/'+guid(target['subscription_id'], 'subscription')

    def read(self):
        if not self.path.exists(): return {}
        require(self.path.stat().st_size<=1024*1024, 'Saved access history exceeds its size limit.')
        try:
            data=json.loads(self.path.read_text(encoding='utf-8'))
            require(isinstance(data,dict) and all(isinstance(r,dict) for r in data.values()), 'Invalid access history.')
            return data
        except (ValueError, OSError) as error:
            raise Stop('Could not read saved access history; the file was preserved. '+str(error)) from error

    def get(self, target): return self.read().get(self.key(target), {})

    def record(self, target, event, **fields):
        require(event in ('clicked','accepted','checked'), 'Unknown access history event.')
        data=self.read(); row=data.setdefault(self.key(target), {}); now=time.time()
        if event=='clicked':
            row.setdefault('first_clicked', now); row['last_clicked']=now
        elif event=='accepted':
            row['assignment_accepted']=now
        else:
            row['last_checked']=now
        row.update(fields)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(self.path, json.dumps(data, indent=2)+'\n')
        return row


def history_text(row, now=None):
    now=time.time() if now is None else now
    lines=[]
    for key,label in (('first_clicked','First Contributor setup click'),('last_clicked','Latest setup click'),
                      ('assignment_accepted','Assignment accepted'),('last_checked','Last access check')):
        value=row.get(key)
        if isinstance(value,(int,float)):
            try:
                stamp=datetime.fromtimestamp(value).astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')
                lines.append(label+': '+stamp)
            except (ValueError, OverflowError, OSError): pass
    if row.get('account'): lines.append('Last checked account: '+row['account'])
    if row.get('status'): lines.append('Last recorded result: '+row['status'])
    accepted=row.get('assignment_accepted')
    if isinstance(accepted,(int,float)) and row.get('ready') is False and 0<=now-accepted<600:
        lines.append('Recent assignment: propagation is possible, but not confirmed. Refresh setup access to check again.')
    if row.get('ready') is False:
        lines.append('If access remains denied, check the active account, subscription scope and CyberQP activation; sign in again if needed.')
    return '\n'.join(lines) or 'No Contributor setup activity recorded for this subscription. Refresh setup access checks Azure without changing roles.'
