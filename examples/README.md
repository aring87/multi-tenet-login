# Example configurations

These files contain placeholders. Copy the appropriate example and fill in your
authorized tenant and workspace details; keep real client data in approved local
storage, such as the ignored `desktop-data/` directory.

| File | Used by |
|---|---|
| `lighthouse-onboarding.example.json` | Current Lighthouse onboarding CLI |
| `onboarding.example.json` | Legacy per-client identity onboarding CLI |
| `azuredeploy.parameters.example.json` | Permissions-only ARM template |
| `client.example.conf` | Permissions-only Git Bash script |

The desktop app creates its configuration and state automatically. Existing saved
clients and settings remain in `desktop-data/` at the repository root.
