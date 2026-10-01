# Optional command-line tools

The desktop launcher remains **Start-Sentinel.cmd** in the repository root.
Run the following Git Bash commands from that root directory:

```bash
bash scripts/onboard-all.sh --help
bash scripts/onboard-permissions.sh --help
```

`onboard-all.sh` starts the legacy per-client identity setup. For current Lighthouse
onboarding use `py -m sentinel_app.lighthouse_onboarding --help` from the root.
See the [onboarding guide](../docs/APP-ONBOARDING.md) before choosing a setup route.

`Rename-Workspace-v2.cmd` and `rename_workspace_v2.py` are an optional standalone
maintenance tool for the older `primary.yml` naming convention. They are not part of
normal onboarding and are not required to upgrade to this folder layout.

Offline Bash checks: `bash tests/run-tests.sh` from the repository root.
