"""Reviewed multi-client assignments, published as one Git commit and draft PR."""
import copy
import difflib
import io
import json
from pathlib import Path
import re
from urllib.parse import quote
import uuid

from .repository_catalog import GitHubReader, parse_yaml, category, MAX_FILE
from .repository_reviews import ReviewService, require, fingerprint, check_path
from .rule_drafts import atomic_write
from . import rule_schema

MAX_REQUEST = 16 * 1024 * 1024
MANIFEST_KEYS = {'version', 'target', 'client', 'enabled', 'allow_missing_mitre', 'azure', 'rules'}


def update_assignment(before, rule_path, enabled):
    """Round-trip comments/quotes, then prove only the intended assignment changed."""
    require(type(enabled) is bool, 'Choose Enabled or Disabled for the assigned rule.')
    original = parse_yaml(before.encode('utf-8'))
    expected = copy.deepcopy(original)
    require(isinstance(expected.get('rules'), list), 'Target rules must be a list.')
    matches = [a for a in expected['rules'] if a.get('path') == rule_path]
    require(len(matches) <= 1, 'Rule is assigned more than once in this target.')
    if matches:
        matches[0].setdefault('overrides', {})['enabled'] = enabled
    else:
        expected['rules'].append({'path': rule_path, 'overrides': {'enabled': enabled}})
    if expected == original:
        return before
    try:
        from ruamel.yaml import YAML
    except ImportError as error:
        raise ValueError('Install the updated application requirements.txt to edit client assignments.') from error
    editor = YAML(typ='rt')
    editor.preserve_quotes = True
    editor.width = 4096
    # The authoritative parser uses YAML 1.1. Refuse semantic drift below rather
    # than changing boolean-like strings or numeric client identifiers implicitly.
    document = editor.load(before.lstrip('\ufeff').replace('\r\n', '\n'))
    selections = document['rules']
    match = next((a for a in selections if a['path'] == rule_path), None)
    if match is None:
        selections.append({'path': rule_path, 'overrides': {'enabled': enabled}})
    else:
        match.setdefault('overrides', {})['enabled'] = enabled
    stream = io.StringIO(); editor.dump(document, stream)
    result = stream.getvalue()
    if before.startswith('\ufeff'): result = '\ufeff' + result
    if '\r\n' in before: result = result.replace('\n', '\r\n')
    require(parse_yaml(result.encode('utf-8')) == expected,
            'YAML formatting would change other settings. Edit this file manually instead.')
    require(len(result.encode('utf-8')) <= MAX_FILE, 'Updated client file exceeds the catalog size limit.')
    return result


def validate_target(path, doc, rules):
    require(set(doc) == MANIFEST_KEYS and type(doc['version']) is int and doc['version'] == 1,
            path + ': unsupported target schema.')
    for field in ('client', 'target'):
        value = doc[field]
        require(isinstance(value,str) and re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*',value)
                and len(value)<=64, path + ': invalid ' + field)
    require(Path(path).parent.name == doc['client'] and doc['target'].startswith(doc['client']+'-'),
            'Client folder, client field and target prefix must agree: '+path)
    require(type(doc['enabled']) is bool and type(doc['allow_missing_mitre']) is bool, 'Invalid target booleans: '+path)
    azure = doc['azure']
    require(isinstance(azure,dict) and set(azure)=={'tenant_id','subscription_id','resource_group','workspace_name','workspace_id'}, 'Invalid Azure fields: '+path)
    require(all(isinstance(v,str) and v.strip() for v in azure.values()), 'Invalid Azure identifiers: '+path)
    if doc['enabled']:
        require(all(rule_schema.guid(azure[k]) for k in ('tenant_id','subscription_id','workspace_id')), 'Enabled target requires valid Azure GUIDs: '+path)
        require(all(re.fullmatch(r'[A-Za-z0-9_.()-]{1,90}',azure[k]) and 'REPLACE' not in azure[k]
                    for k in ('resource_group','workspace_name')), 'Invalid workspace or resource group: '+path)
    ids, paths, warnings = set(), set(), []
    for assignment in doc['rules']:
        require(isinstance(assignment,dict) and 'path' in assignment and not(set(assignment)-{'path','overrides'}), 'Invalid rule selection: '+path)
        source = assignment['path']
        require(source in rules and source not in paths, 'Missing or duplicate rule selection: '+str(source))
        paths.add(source)
        effective = rule_schema.apply_overrides(rules[source],assignment.get('overrides',{}))
        warnings += [doc['target']+': '+w for w in rule_schema.validate(effective,doc['allow_missing_mitre'])]
        rule_schema.to_arm(effective)
        require(effective['id'].lower() not in ids, 'Duplicate effective rule ID in '+doc['target'])
        ids.add(effective['id'].lower())
    return warnings


def changes(snapshot, rule_path, targets, enabled):
    check_path(rule_path)
    require(type(enabled) is bool,'Choose Enabled or Disabled.')
    require(isinstance(targets,list) and 0<len(targets)<=100 and all(isinstance(t,str) for t in targets)
            and len(set(targets))==len(targets), 'Select between 1 and 100 distinct workspaces.')
    require(not snapshot['issues'],'Resolve catalog issues before assigning rules.')
    rules = {r['path']:r['raw'] for r in snapshot['rules']}
    require(rule_path in rules,'The selected rule no longer exists. Refresh the catalog.')
    clients = {c['target']:c for c in snapshot['clients']}
    files, impact, warnings = [], [], []
    for target in sorted(targets):
        require(target in clients,'Unknown target: '+target)
        client = clients[target]; path = client['path']; entry = snapshot['entries'].get(path,{})
        require(category(path)=='clients' and entry.get('type')=='blob' and entry.get('mode') in ('100644','100755'), 'Unsupported client file: '+path)
        before = snapshot['files'][path].decode('utf-8')
        content = update_assignment(before,rule_path,enabled)
        doc = parse_yaml(content.encode('utf-8'))
        warnings += validate_target(path,doc,rules)
        assignment = next(a for a in doc['rules'] if a['path']==rule_path)
        previous = next((a for a in client['assignments'] if a['path']==rule_path),None)
        action = 'Add assignment' if previous is None else 'Update enabled override' if content!=before else 'Already configured'
        impact.append(dict(target=target,client=client['name'],workspace=client['workspace'],
                           target_enabled=doc['enabled'],enabled=enabled,action=action,overrides=assignment['overrides']))
        if not doc['enabled']: warnings.append(target+': target remains disabled; assigning a rule does not enable the workspace target.')
        if content!=before: files.append(dict(path=path,mode=entry['mode'],before=before,content=content))
    require(files,'These workspaces already have the requested assignment. No changes to submit.')
    return files,impact,sorted(set(warnings))


def assignment_diff(plan):
    return '\n'.join(''.join(difflib.unified_diff(f['before'].splitlines(True),f['content'].splitlines(True),
                     fromfile='before/'+f['path'],tofile='after/'+f['path'])) for f in plan['files'])


def check_record(record):
    require(isinstance(record,dict) and record.get('version')==1 and record.get('kind')=='assignments', 'Unsupported assignment request.')
    p=record.get('plan')
    require(isinstance(p,dict) and record.get('fingerprint')==fingerprint(p),'Assignment review changed. Prepare a new review.')
    require(re.fullmatch(r'[0-9a-f]{32}',p.get('request_id','')),'Invalid request ID.')
    require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+',p.get('repository','')),'Invalid repository.')
    require(p.get('base_branch')=='main','Assignment reviews require main as the default branch.')
    for key in ('base_sha','tree_sha'):
        require(re.fullmatch(r'[0-9a-f]{40}',p.get(key,'')),'Invalid base revision.')
    if record.get('commit_sha') is not None:
        require(re.fullmatch(r'[0-9a-f]{40}',record['commit_sha']),'Invalid saved commit.')
    check_path(p['rule_path'])
    require(type(p['enabled']) is bool and isinstance(p['files'],list) and 0<len(p['files'])<=100,'Invalid assignment changes.')
    for f in p['files']:
        require(category(f['path'])=='clients' and f['mode'] in ('100644','100755'),'Invalid client file.')
        require(isinstance(f['before'],str) and isinstance(f['content'],str),'Invalid file content.')
    require(len((json.dumps(record,indent=2)+'\n').encode())<=MAX_REQUEST,'Assignment request is too large; select fewer workspaces.')
    return p


def save_request(folder,record):
    p=check_record(record)
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    path=folder/(p['request_id']+'.assignment.json')
    atomic_write(path,json.dumps(record,indent=2)+'\n')
    return path


def load_request(path):
    with Path(path).open('rb') as stream: raw=stream.read(MAX_REQUEST+1)
    require(len(raw)<=MAX_REQUEST,'Assignment request is too large.')
    record=json.loads(raw);check_record(record);return record


class AssignmentService(ReviewService):
    validate_record=staticmethod(check_record)
    save_record=staticmethod(save_request)

    def prepare(self,repository,revision,rule_path,targets,enabled=False):
        snapshot=GitHubReader(lambda endpoint:self.request('GET',endpoint)).load(repository)
        require(snapshot['private'] is True and snapshot['can_push'] is True,'Use a private detection repository with GitHub write access.')
        require(snapshot['base_branch']=='main','Assignment reviews require main as the default branch.')
        require(snapshot['revision']==revision,'The repository changed. Refresh the catalog before assigning rules.')
        files,impact,warnings=changes(snapshot,rule_path,targets,enabled)
        name=next(r['name'] for r in snapshot['rules'] if r['path']==rule_path)
        p=dict(request_id=uuid.uuid4().hex,repository=repository,repository_id=snapshot['repository_id'],
               identity=snapshot['identity'],base_branch='main',base_sha=revision,tree_sha=snapshot['tree_sha'],
               name=name,rule_path=rule_path,targets=sorted(targets),enabled=enabled,files=files,impact=impact,warnings=warnings)
        require(p['repository_id'] is not None,'GitHub did not identify the repository.')
        record=dict(version=1,kind='assignments',plan=p,fingerprint=fingerprint(p),commit_sha=None,pull_request=None)
        check_record(record);return record

    def submit(self,record,folder):
        p=check_record(record)
        # A saved request is untrusted. Rebuild its edits from immutable base files
        # before publishing, including recovery after main has advanced.
        prefix='repos/'+p['repository']
        def at_base(endpoint):
            if endpoint.startswith(prefix+'/commits/'):
                endpoint=prefix+'/commits/'+p['base_sha']
            return self.request('GET',endpoint)
        snapshot=GitHubReader(at_base).load(p['repository'])
        require(snapshot['revision']==p['base_sha'] and snapshot['tree_sha']==p['tree_sha'],'Reviewed base does not match GitHub.')
        files,impact,warnings=changes(snapshot,p['rule_path'],p['targets'],p['enabled'])
        require(files==p['files'] and impact==p['impact'] and warnings==p['warnings'],
                'Saved assignment edits differ from the reviewed base. Prepare a new request.')
        return super().submit(record,folder)

    def tree_entries(self,plan):
        return [dict(path=f['path'],mode=f['mode'],type='blob',content=f['content']) for f in plan['files']]

    def branch_name(self,plan): return 'codex/assign-'+plan['request_id']

    def change_title(self,plan): return 'Assign Sentinel rule: '+plan['name'][:160]

    def pull_body(self,plan):
        return ('Assigns `'+plan['rule_path']+'` to the reviewed client workspaces.\n\n'
                'Requested rule state: '+('Enabled' if plan['enabled'] else 'Disabled')+'. '
                'Existing overrides are preserved except for the explicitly reviewed enabled value. '
                'Workspace enablement, Azure destinations and the shared rule file are unchanged.\n\n'
                +'\n'.join('- '+c['target']+': '+c['action'] for c in plan['impact'])
                +'\n\nRepository CI and human review remain required. After merge, refresh the catalog, '
                'preview the selected rule, and use the separate reviewed deployment flow. '
                'This action does not merge or dispatch deployments.\n\n'
                +'\n'.join(plan['warnings'])+'\n\nRequest: `'+plan['request_id']+'`\nBase: `'+plan['base_sha']+'`')
