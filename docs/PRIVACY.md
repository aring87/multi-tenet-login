# Privacy and publication notes

## Public source
The application starts with no clients or company configuration. GitHub organization and repository fields are blank. Resource names in examples are fictional, and example GUIDs are placeholders or synthetic test fixtures.

This repository contains source, templates, documentation and offline tests. It does not distribute session caches, tokens, passwords, actual client inventories or onboarding state.

## Local operation
Azure CLI manages authentication. The application uses a separate Azure CLI configuration folder for each session. This folder may contain cached authentication tokens. Windows account-broker and browser sessions are managed by Microsoft separately.

The app writes local administration data beneath desktop-data/, including saved client mappings, session configuration and resumable setup state. That folder is ignored by Git. Do not upload its contents in issues, screenshots, support bundles or pull requests.

Azure and GitHub receive requests when their features are used. This app does not send client data to an AI service or an app-operated analytics endpoint. The underlying CLIs retain their own documented behavior and settings.

Deleting a client removes its saved dropdown entry and invalidates the current selection/preview. It retains onboarding state and does not delete Azure, GitHub or access-provider resources.

## Publishing contributions
Use fictional client and resource names. Keep authentication state, credentials, actual tenant/customer records and generated local configuration out of commits. Review screenshots and logs before sharing them.

Public GitHub repository ownership and commit attribution remain visible as normal GitHub metadata. They are separate from client/company configuration.

## Scope
This is a Windows application for Azure public cloud and github.com. Access depends on the signed-in account's permissions. CyberQP is an optional external access-management service, not a dependency or affiliation. Full onboarding targets an existing compatible private Sentinel pipeline; it is not a universal pipeline installer.
