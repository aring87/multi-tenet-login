#!/usr/bin/env bash
# Offline regression tests. All az calls resolve to the temporary stub below.
set -Eeuo pipefail
test_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
script_dir="$(cd -- "$test_dir/.." && pwd)"
fixture="$(mktemp -d)"
mkdir -p "$fixture/fake-bin" "$fixture/package with spaces"
cp "$script_dir/onboard-permissions.sh" "$script_dir/azuredeploy.json" "$fixture/package with spaces/"
sut="$fixture/package with spaces/onboard-permissions.sh"
cat > "$fixture/fake-bin/az" <<'FAKE'
#!/usr/bin/env bash
set -euo pipefail
[[ "${MSYS_NO_PATHCONV:-}" == 1 && "${MSYS2_ARG_CONV_EXCL:-}" == '*' ]] || exit 90
printf '%s\n' "$*" >> "$AZ_TEST_LOG"
option() {
  local wanted="$1"; shift
  while (( $# )); do
    if [[ "$1" == "$wanted" ]]; then printf '%s' "$2"; return; fi
    shift
  done
  return 1
}
case "${1:-} ${2:-} ${3:-}" in
  'version '*)
    [[ "$AZ_TEST_CASE" != old-cli ]] || { printf '2.50.0\n'; exit 0; }
    printf '2.80.0\r\n' ;;
  'account show '*)
    if [[ "$AZ_TEST_CASE" == wrong-tenant ]]; then printf '99999999-9999-9999-9999-999999999999\n'
    else printf '11111111-1111-1111-1111-111111111111\r\n'; fi ;;
  'account set '*) exit 0 ;;
  'monitor log-analytics workspace')
    if [[ "$AZ_TEST_CASE" == wrong-workspace ]]; then printf '/subscriptions/wrong/resourceGroups/wrong\n'
    else printf '/subscriptions/22222222-2222-2222-2222-222222222222/resourceGroups/Sentinel RG/providers/Microsoft.OperationalInsights/workspaces/Client-Workspace\r\n'; fi ;;
  'ad sp show')
    id="$(option --id "$@")"
    query="$(option --query "$@")"
    if [[ "$AZ_TEST_CASE" == directory-denied ]]; then exit 7; fi
    if [[ "$query" == id ]]; then
      if [[ "$AZ_TEST_CASE" == client-id ]]; then printf '88888888-8888-8888-8888-888888888888\n'
      else printf '%s\r\n' "$id"; fi
    elif [[ "$query" == displayName ]]; then printf 'Test application %s\n' "$id"
    else exit 91; fi ;;
  'deployment group validate')
    [[ "$AZ_TEST_CASE" != validation-failed ]] || exit 8 ;;
  'deployment group what-if')
    [[ "$AZ_TEST_CASE" != whatif-failed ]] || exit 8
    printf 'Mock what-if: 4 role definitions and 4 role assignments.\n' ;;
  'deployment group create')
    [[ "$AZ_TEST_CASE" != create-failed ]] || exit 8
    printf 'Succeeded\r\n' ;;
  'deployment group show') printf '{"targetName":{"value":"acme-primary"}}\n' ;;
  *) printf 'Unexpected mocked az command: %s\n' "$*" >&2; exit 99 ;;
esac
FAKE
chmod +x "$fixture/fake-bin/az"
cat > "$fixture/client one.conf" <<'CONFIG'
TARGET=acme-primary
TENANT_ID=11111111-1111-1111-1111-111111111111
SUBSCRIPTION_ID=22222222-2222-2222-2222-222222222222
RESOURCE_GROUP=Sentinel RG
WORKSPACE_NAME=Client-Workspace
PREVIEW_SP_OBJECT_ID=33333333-3333-3333-3333-333333333333
DEPLOY_SP_OBJECT_ID=44444444-4444-4444-4444-444444444444
CONFIG
good="$fixture/client one.conf"
sed 's/44444444-4444-4444-4444-444444444444/33333333-3333-3333-3333-333333333333/' "$good" > "$fixture/same.conf"
cp "$good" "$fixture/duplicate.conf"
printf 'TARGET=another-target\n' >> "$fixture/duplicate.conf"
cp "$good" "$fixture/unknown.conf"
printf 'UNKNOWN_KEY=value\n' >> "$fixture/unknown.conf"
sed 's/11111111-1111-1111-1111-111111111111/REPLACE-ME/' "$good" > "$fixture/placeholder.conf"
# Config text is data, not shell code. This marker must never be created.
cp "$good" "$fixture/injection.conf"
printf 'UNKNOWN_KEY=$(touch "%s")\n' "$fixture/executed-marker" >> "$fixture/injection.conf"
sed 's/$/\r/' "$good" > "$fixture/crlf.conf"
passed=0
run_case() {
  local case_name="$1" config="$2" expected="$3" apply_mode="$4" furthest="$5"
  local log="$fixture/$case_name.log" output="$fixture/$case_name.output" status=0 calls=''
  local flags=()
  [[ "$apply_mode" == apply ]] && flags+=(--apply)
  : > "$log"
  env PATH="$fixture/fake-bin:$PATH" AZ_TEST_CASE="$case_name" AZ_TEST_LOG="$log" \
    bash "$sut" --config "$config" "${flags[@]}" > "$output" 2>&1 || status=$?
  if [[ "$expected" == success && $status -ne 0 ]] || [[ "$expected" == failure && $status -eq 0 ]]; then
    cat "$output"; printf 'FAIL: %s returned %s\n' "$case_name" "$status"; exit 1
  fi
  calls="$(<"$log")"
  case "$furthest" in
    no-az) [[ -z "$calls" ]] ;;
    no-deployment) [[ "$calls" != *'deployment group '* ]] ;;
    validate-only) [[ "$calls" == *'deployment group validate'* && "$calls" != *'deployment group what-if'* && "$calls" != *'deployment group create'* ]] ;;
    whatif) [[ "$calls" == *'deployment group what-if'* && "$calls" != *'deployment group create'* ]] ;;
    create-failure) [[ "$calls" == *'deployment group create'* && "$calls" != *'deployment group show'* ]] ;;
    applied) [[ "$calls" == *'deployment group validate'* && "$calls" == *'deployment group what-if'* && "$calls" == *'deployment group create'* && "$calls" == *'deployment group show'* ]] ;;
    *) exit 92 ;;
  esac || { printf 'FAIL: unexpected execution stage for %s\n%s\n' "$case_name" "$calls"; exit 1; }
  passed=$((passed + 1))
  printf 'PASS %s\n' "$case_name"
}
run_case preview "$good" success preview whatif
run_case apply "$good" success apply applied
run_case same-principals "$fixture/same.conf" failure apply no-az
run_case wrong-tenant "$good" failure apply no-deployment
run_case client-id "$good" failure apply no-deployment
run_case wrong-workspace "$good" failure apply no-deployment
run_case directory-denied "$good" failure apply no-deployment
run_case validation-failed "$good" failure apply validate-only
run_case whatif-failed "$good" failure apply whatif
run_case create-failed "$good" failure apply create-failure
run_case duplicate "$fixture/duplicate.conf" failure apply no-az
run_case unknown "$fixture/unknown.conf" failure apply no-az
run_case placeholder "$fixture/placeholder.conf" failure apply no-az
run_case injection "$fixture/injection.conf" failure apply no-az
[[ ! -e "$fixture/executed-marker" ]] || { printf 'FAIL: config executed shell code\n'; exit 1; }
run_case crlf "$fixture/crlf.conf" success preview whatif
run_case old-cli "$good" failure apply no-deployment
printf '\n%d offline cases passed. No real Azure CLI or tenant was contacted.\nFixture logs: %s\n' "$passed" "$fixture"
