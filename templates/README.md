# Azure deployment templates

- `lighthouse-onboard.json`: subscription-level Lighthouse delegation used by current onboarding.
- `azuredeploy.json`: resource-group-level permissions template used by the older permissions setup routes.

Application code and scripts resolve these paths relative to the repository, so keep
this folder intact. Parameter examples are in [examples](../examples/).
These files are setup templates; they do not contain the private repository's detection rules.
