"""Register an existing managing-tenant workspace without Azure mutations."""
from urllib.parse import quote

from .lighthouse_onboarding import Onboard, guid, require, validate_config
from .repository_catalog import GitHubReader

# Require the documented built-in access model; do not infer equivalent custom roles.
ROLES = (
    ('deploy_group_object_id', 'Microsoft Sentinel Contributor', 'ab8e14d6-4a74-4a29-9ba8-549422addade'),
    ('read_group_object_id', 'Microsoft Sentinel Reader', '8d289c81-5878-46d4-8554-54e1e3d8b5cb'),
    ('read_group_object_id', 'Log Analytics Reader', '73c42c96-874c-492b-b04d-ab87d138a893'),
    ('engineer_group_object_id', 'Microsoft Sentinel Reader', '8d289c81-5878-46d4-8554-54e1e3d8b5cb'),
    ('engineer_group_object_id', 'Reader', 'acdd72a7-3385-48ef-bd42-f606fba81ae7'),
)


def validate_app_config(config):
    return validate_config(config, allow_same_tenant=True)


def onboarding_mode(config):
    return ('same-tenant' if config['tenant_id'].lower() == config['managing_tenant_id'].lower()
            else 'lighthouse-delegation')


def review_text(config):
    direct = onboarding_mode(config) == 'same-tenant'
    title = 'Same-tenant workspace registration' if direct else 'Lighthouse delegation onboarding'
    summary = (title + '\nTarget: ' + config['client'] + '-' + config['workspace_label']
               + '\nClient tenant: ' + config['tenant_id']
               + '\nManaging tenant: ' + config['managing_tenant_id']
               + '\nSubscription: ' + config['subscription_id']
               + '\nWorkspace: ' + config['workspace_name']
               + '\nResource group: ' + config['resource_group'])
    action = ('This verifies existing shared-group permissions and opens the client target-file '
              'and dropdown pull request. No Azure resources or role assignments are changed.'
              if direct else
              'This delegates the client subscription to the managing tenant and opens a target-file pull request.')
    return summary, action


class ReadOnlyAzure:
    """Keep inherited repository-writing helpers from ever writing to Azure."""
    def __init__(self, io):
        self.io = io
        self.apply = io.apply

    def az(self, *args, write=False):
        require(not write, 'Same-tenant registration cannot change Azure resources or permissions.')
        return self.io.az(*args)

    def gh(self, *args, **kwargs):
        return self.io.gh(*args, **kwargs)

    def pages(self, *args, **kwargs):
        return self.io.pages(*args, **kwargs)


class SameTenantOnboard(Onboard):
    validate = staticmethod(validate_app_config)

    def __init__(self, config, cli, state_path, template_path):
        require(onboarding_mode(validate_app_config(config)) == 'same-tenant',
                'Direct workspace registration requires the managing tenant.')
        super().__init__(config, ReadOnlyAzure(cli), state_path, template_path)
        self.resource_group_scope = self.scope + '/resourceGroups/' + self.c['resource_group']

    def preflight(self):
        require(self.io.az('cloud', 'show')['name'] == 'AzureCloud',
                'This workflow supports Azure Public workspaces (Commercial or GCC). '
                'Azure Government requires a separate configuration.')
        account = self.io.az('account', 'show')
        require(account['tenantId'].lower() == self.c['tenant_id'] and
                account['id'].lower() == self.c['subscription_id'],
                'Session/destination mismatch. Sign in to the managing tenant and select this subscription.')
        self.workspace = self.io.az('monitor', 'log-analytics', 'workspace', 'show',
            '--subscription', self.c['subscription_id'], '--resource-group', self.c['resource_group'],
            '--workspace-name', self.c['workspace_name'])
        expected = self.resource_group_scope + '/providers/Microsoft.OperationalInsights/workspaces/' + self.c['workspace_name']
        require(self.workspace['id'].lower() == expected.lower(), 'Azure returned a different workspace.')
        guid(self.workspace['customerId'], 'workspace GUID')
        self.check_repo()
        self.check_shared_environments()
        self.check_groups()
        self.check_direct_roles()
        self.check_duplicate_workspace()
        self.inspect_target()

    def check_groups(self):
        for field in ('deploy_group_object_id', 'read_group_object_id', 'engineer_group_object_id'):
            group = self.io.az('ad', 'group', 'show', '--group', self.c[field])
            require(group.get('id', '').lower() == self.c[field] and group.get('securityEnabled') is True,
                    'The configured ' + field + ' is not a security group in the signed-in tenant.')
        apps = []
        for env, field in (('preview_environment', 'read_group_object_id'),
                           ('production_environment', 'deploy_group_object_id')):
            name = self.c[env]
            value = self.io.gh('GET', self.repo_path + '/environments/' + quote(name, safe='')
                               + '/variables/AZURE_CLIENT_ID')
            app_id = guid(value['value'], name + ' AZURE_CLIENT_ID')
            apps.append(app_id)
            sp = self.io.az('ad', 'sp', 'show', '--id', app_id)
            require(sp.get('appId', '').lower() == app_id, 'Pipeline application lookup returned a different identity.')
            member = self.io.az('ad', 'group', 'member', 'check', '--group', self.c[field],
                                '--member-id', guid(sp.get('id'), 'pipeline principal ID'))
            require(member.get('value') is True,
                    name + ' pipeline identity is not a member of the configured ' + field +
                    '. Correct group membership, then preview again.')
        require(len(set(apps)) == 2, 'Preview and production must use different pipeline applications.')

    def check_direct_roles(self):
        assignments = self.io.az('role', 'assignment', 'list', '--scope', self.resource_group_scope,
                                 '--include-inherited', '--fill-principal-name', 'false')
        require(isinstance(assignments, list), 'Azure returned an invalid role assignment list.')
        scopes = {self.resource_group_scope.lower(), self.scope.lower(), '/'}
        self.access_rows = []
        missing = []
        for field, name, role_id in ROLES:
            matches = [a for a in assignments
                       if a.get('principalId', '').lower() == self.c[field]
                       and a.get('roleDefinitionId', '').rsplit('/', 1)[-1].lower() == role_id
                       and a.get('scope', '').lower() in scopes and not a.get('condition')]
            if not matches:
                missing.append(name + ' for ' + field + ' (' + self.c[field] + ')')
            else:
                self.access_rows.append(name + ' -> ' + self.c[field] + ' at ' + matches[0]['scope'])
        require(not missing, 'Cannot confirm the expected direct group permissions at ' +
                self.resource_group_scope + ':\n' + '\n'.join(missing) +
                '\nAsk your Azure access administrator to review these built-in role assignments. '
                'Custom, conditional, management-group or alternative broader roles are not evaluated. '
                'No permissions were changed. Then preview again.')

    def check_duplicate_workspace(self):
        snapshot = GitHubReader(lambda endpoint: self.io.gh('GET', endpoint)).load(
            self.c['github_owner'] + '/' + self.c['github_repo'])
        require(not snapshot['issues'], 'Resolve repository catalog issues before registering a workspace:\n' +
                '\n'.join(snapshot['issues']))
        expected_path = 'clients/' + self.c['client'] + '/' + self.c['target'] + '.yml'
        for client in snapshot['clients']:
            azure = client['raw']['azure']
            destination = all(str(azure.get(k, '')).lower() == self.c[k].lower()
                              for k in ('subscription_id', 'resource_group', 'workspace_name'))
            customer = str(azure.get('workspace_id', '')).lower() == self.workspace['customerId'].lower()
            if destination or customer or client['target'] == self.c['target']:
                require(client['path'] == expected_path,
                        'This workspace or target is already registered at ' + client['path'] +
                        '. Use that existing target; no duplicate client file was created.')

    def access_description(self):
        return ('Adds the client workspace target in the managing tenant. Existing built-in group '
                'permissions and pipeline group membership were checked. No Lighthouse delegation, '
                'Azure resource, role assignment, identity or environment was created. ')

    def run(self):
        self.preflight()
        print('Same-tenant workspace registration')
        print('Target: ' + self.c['target'])
        print('Workspace: ' + self.workspace['id'])
        print('Existing group access:')
        for row in self.access_rows:
            print('  ' + row)
        print('Shared pipeline group membership and GitHub environments verified.')
        print('Plan: create the target-file and dropdown pull request. Azure permissions stay unchanged.')
        if not self.apply:
            print('READ-ONLY PLAN. No GitHub or Azure resources changed.')
            return
        self.create_target_pr()
        print('Workspace registration prepared. Merge the reviewed PR and run the pipeline preview on main.')
