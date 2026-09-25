#!/usr/bin/env bash
# RBAC onboarding only. Existing client-tenant preview/deploy service principals required.
set -Eeuo pipefail
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
usage() {
  cat <<'USAGE'
Usage:
  bash onboard-permissions.sh --config clients/acme-primary.conf
  bash onboard-permissions.sh --config clients/acme-primary.conf --apply

Default: check tenant, workspace and service principal IDs, validate, then show what-if.
--apply: perform those same checks, then create/update the four RBAC roles and assignments.
Sign in first using the authorized client onboarding account: az login --tenant TENANT_ID
This script does not create Entra applications, groups, OIDC credentials or GitHub environments.
USAGE
}
(( BASH_VERSINFO[0] >= 4 )) || die 'Bash 4 or later is required (current Git for Windows includes it).'
config_path=''
apply=false
while (( $# )); do
  case "$1" in
    --config)
      [[ $# -ge 2 && -n "$2" && "$2" != --* ]] || die '--config requires a file path.'
      [[ -z "$config_path" ]] || die '--config was supplied more than once.'
      config_path="$2"; shift 2 ;;
    --apply) apply=true; shift ;;
    --help|-h) usage; exit 0 ;;
    *) die "Unknown argument: $1" ;;
  esac
done
[[ -n "$config_path" && -f "$config_path" ]] || die 'Supply an existing --config file. See client.example.conf.'
trim() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}
declare -A cfg=()
line_number=0
while IFS= read -r line || [[ -n "$line" ]]; do
  line_number=$((line_number + 1))
  line="${line%$'\r'}"
  line="$(trim "$line")"
  [[ -z "$line" || "$line" == \#* ]] && continue
  [[ "$line" == *=* ]] || die "Config line $line_number must be KEY=value."
  key="$(trim "${line%%=*}")"
  value="$(trim "${line#*=}")"
  case "$key" in
    TARGET|TENANT_ID|SUBSCRIPTION_ID|RESOURCE_GROUP|WORKSPACE_NAME|PREVIEW_SP_OBJECT_ID|DEPLOY_SP_OBJECT_ID) ;;
    *) die "Unknown config key at line $line_number: $key" ;;
  esac
  [[ -z "${cfg[$key]+present}" ]] || die "Duplicate config key: $key"
  [[ -n "$value" && ! "$value" =~ [[:cntrl:]] ]] || die "Empty or invalid value for $key."
  cfg["$key"]="$value"
done < "$config_path"

for key in TARGET TENANT_ID SUBSCRIPTION_ID RESOURCE_GROUP WORKSPACE_NAME PREVIEW_SP_OBJECT_ID DEPLOY_SP_OBJECT_ID; do
  [[ -n "${cfg[$key]:-}" ]] || die "Missing config value: $key"
done
guid_pattern='^[[:xdigit:]]{8}-[[:xdigit:]]{4}-[[:xdigit:]]{4}-[[:xdigit:]]{4}-[[:xdigit:]]{12}$'
for key in TENANT_ID SUBSCRIPTION_ID PREVIEW_SP_OBJECT_ID DEPLOY_SP_OBJECT_ID; do
  [[ "${cfg[$key]}" =~ $guid_pattern ]] || die "$key must be a GUID; replace placeholders and omit quote marks."
  cfg["$key"]="${cfg[$key],,}"
done
[[ "${cfg[TARGET]}" =~ ^[a-z0-9]+(-[a-z0-9]+)*$ && ${#cfg[TARGET]} -ge 3 && ${#cfg[TARGET]} -le 48 ]] || die 'TARGET must be a lowercase slug, 3-48 characters.'
[[ "${cfg[WORKSPACE_NAME]}" =~ ^[a-zA-Z0-9][a-zA-Z0-9-]{2,61}[a-zA-Z0-9]$ ]] || die 'WORKSPACE_NAME must be a workspace resource name, 4-63 characters.'
[[ ${#cfg[RESOURCE_GROUP]} -le 90 && "${cfg[RESOURCE_GROUP]}" != *. ]] || die 'Invalid resource group name.'
[[ "${cfg[PREVIEW_SP_OBJECT_ID]}" != "${cfg[DEPLOY_SP_OBJECT_ID]}" ]] || die 'Preview and deployment must use different service principal Object IDs.'

command -v az >/dev/null 2>&1 || die 'Azure CLI is not on PATH. Install it and reopen Git Bash.'
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
template_path="$script_dir/azuredeploy.json"
[[ -f "$template_path" ]] || die 'Keep azuredeploy.json beside this script.'
# Stop Git Bash from rewriting /subscriptions/... as a Windows filesystem path.
# Convert the one local template path explicitly for the native Windows Azure CLI.
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'
if command -v cygpath >/dev/null 2>&1; then
  template_path="$(cygpath -m "$template_path")"
fi
clean_tsv() { tr -d '\r'; }
cli_version="$(az version --query '"azure-cli"' --output tsv --only-show-errors | clean_tsv)" || die 'Could not read Azure CLI version.'
IFS=. read -r cli_major cli_minor cli_patch <<< "$cli_version"
[[ "$cli_major" =~ ^[0-9]+$ && "$cli_minor" =~ ^[0-9]+$ ]] || die "Unexpected Azure CLI version: $cli_version"
(( 10#$cli_major > 2 || (10#$cli_major == 2 && 10#$cli_minor >= 76) )) || die 'Azure CLI 2.76.0 or later is required.'

# First check the requested subscription without changing the active account.
actual_tenant="$(az account show --subscription "${cfg[SUBSCRIPTION_ID]}" --query tenantId --output tsv --only-show-errors | clean_tsv)" ||
  die "Sign in to the client first: az login --tenant ${cfg[TENANT_ID]}"
[[ "${actual_tenant,,}" == "${cfg[TENANT_ID]}" ]] || die 'Requested subscription belongs to a different tenant. No deployment was attempted.'
# Graph service-principal checks use the active tenant, so select and recheck it.
az account set --subscription "${cfg[SUBSCRIPTION_ID]}" --only-show-errors
active_tenant="$(az account show --query tenantId --output tsv --only-show-errors | clean_tsv)"
[[ "${active_tenant,,}" == "${cfg[TENANT_ID]}" ]] || die 'Active Azure tenant did not match the config.'

workspace_id="$(az monitor log-analytics workspace show \
  --subscription "${cfg[SUBSCRIPTION_ID]}" --resource-group "${cfg[RESOURCE_GROUP]}" \
  --workspace-name "${cfg[WORKSPACE_NAME]}" --query id --output tsv --only-show-errors | clean_tsv)" ||
  die 'Cannot read the configured workspace. Check its name, resource group and your access.'
expected_workspace="/subscriptions/${cfg[SUBSCRIPTION_ID]}/resourceGroups/${cfg[RESOURCE_GROUP]}/providers/Microsoft.OperationalInsights/workspaces/${cfg[WORKSPACE_NAME]}"
[[ "${workspace_id,,}" == "${expected_workspace,,}" ]] || die 'Workspace returned by Azure does not match the configured destination.'

for key in PREVIEW_SP_OBJECT_ID DEPLOY_SP_OBJECT_ID; do
  resolved_id="$(az ad sp show --id "${cfg[$key]}" --query id --output tsv --only-show-errors | clean_tsv)" ||
    die "Cannot verify $key in this tenant. Check the Enterprise application Object ID and your directory-read access."
  [[ "${resolved_id,,}" == "${cfg[$key]}" ]] ||
    die "$key is not the service principal Object ID. Use Enterprise applications > application > Object ID."
done
preview_name="$(az ad sp show --id "${cfg[PREVIEW_SP_OBJECT_ID]}" --query displayName --output tsv --only-show-errors | clean_tsv)"
deploy_name="$(az ad sp show --id "${cfg[DEPLOY_SP_OBJECT_ID]}" --query displayName --output tsv --only-show-errors | clean_tsv)"
printf '\nTarget: %s\nTenant: %s\nSubscription: %s\nWorkspace: %s\nPreview: %s (%s)\nDeployment: %s (%s)\n\n' \
  "${cfg[TARGET]}" "${cfg[TENANT_ID]}" "${cfg[SUBSCRIPTION_ID]}" "$workspace_id" \
  "$preview_name" "${cfg[PREVIEW_SP_OBJECT_ID]}" "$deploy_name" "${cfg[DEPLOY_SP_OBJECT_ID]}"

deployment_name="sentinel-rbac-${cfg[TARGET]}"
common=(
  --subscription "${cfg[SUBSCRIPTION_ID]}"
  --resource-group "${cfg[RESOURCE_GROUP]}"
  --name "$deployment_name"
  --template-file "$template_path"
  --mode Incremental
  --parameters
  "targetName=${cfg[TARGET]}"
  "workspaceName=${cfg[WORKSPACE_NAME]}"
  "previewPrincipalObjectId=${cfg[PREVIEW_SP_OBJECT_ID]}"
  "deploymentPrincipalObjectId=${cfg[DEPLOY_SP_OBJECT_ID]}"
  --only-show-errors
)
printf 'Validating the onboarding template with your onboarding account...\n'
az deployment group validate "${common[@]}" --validation-level Provider --output none ||
  die 'Azure validation failed. No role changes were submitted.'
printf 'Showing proposed permission changes...\n'
az deployment group what-if "${common[@]}" --validation-level Provider ||
  die 'Azure what-if failed. No role changes were submitted.'
if [[ "$apply" != true ]]; then
  printf '\nPreview complete; no role changes were submitted.\nReview the changes, then rerun this command with --apply to deploy.\n'
  exit 0
fi
printf '\nApplying the reviewed RBAC template...\n'
state="$(az deployment group create "${common[@]}" --query properties.provisioningState --output tsv | clean_tsv)" ||
  die 'Deployment failed. Check the Azure deployment operations; partial changes may exist.'
[[ "$state" == Succeeded ]] || die "Deployment ended with state '$state'. Inspect its operations before retrying."
az deployment group show --subscription "${cfg[SUBSCRIPTION_ID]}" \
  --resource-group "${cfg[RESOURCE_GROUP]}" --name "$deployment_name" \
  --query properties.outputs --output json --only-show-errors ||
  die 'Deployment succeeded but outputs could not be retrieved; inspect it in the Azure portal.'
printf '\nOnboarding permissions deployed. Verify effective IAM access, then run the GitHub preview/deploy test.\n'
