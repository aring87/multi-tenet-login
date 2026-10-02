# Verification

The desktop and onboarding tests run entirely against simulated Azure/GitHub responses.
No client credentials or cloud deployments are required for the tests.

Checks cover discovery parsing, per-session configuration, preview/apply order, destination and identity conflicts, deletion confirmation and local-only effects, empty client inventory, resumed onboarding, and generated workspace filenames.

Run: python -m unittest discover -s tests -p "test_*.py" -v

The permissions-only Bash test harness uses an Azure CLI stub and can be run separately with Git Bash: bash tests/run-tests.sh.

Live Microsoft sign-in, tenant policy, Azure provider permissions and actual GitHub API behavior still require an authorized pilot. The private detection pipeline itself is outside this repository.

## Audit evidence validation (2026-09-26)

Added read-only Azure/Sentinel evidence collection and exports. 82 offline tests
passed with the installed Python 3.10 runtime, including 22 new evidence/backend
and desktop-action checks. Existing discovery/onboarding tests remain included.
Windows Tkinter emits teardown warnings in the multi-root test suite; assertions
pass. Tests require access to the Windows UI runtime outside the restricted sandbox.

Checked the Audit Evidence page at the minimum 1120x740 window size and adjusted
paragraph wrapping. Live Azure collection is pending: the installed app's saved
session returned "Please run az login". No live client evidence was collected.
Use the acceptance steps in AUDIT-EVIDENCE.md before treating live collection as
validated. Framework mapping and compliance conclusions remain outside this release.

## Coverage and findings validation (2026-09-26)

Version 2 adds bounded log-query splitting, daily evidence coverage, optional
workspace-specific requirements and findings linked to original evidence records.
112 offline tests passed on the installed Python 3.10 runtime, including 30 new
checks for split recovery, unresolved ranges, query/row limits, current-day
coverage, conservative findings, evidence links and isolated requirements storage.
The report was exercised in a browser using synthetic data: findings filtering,
source links and report layout worked. The desktop requirements editor was
checked at the minimum window size. Existing Tkinter teardown warnings remain in
the multi-root test suite; assertions pass.

The user reported a successful live collection with version 1. Version 2's new
splitting/coverage/findings behavior has not yet been validated on a live tenant.
No client data was used for the synthetic report or included in this change.
## Sentinel configuration and optional logs (2026-09-28)

Version 3 replaces the broad audit workflow with four selectable configuration
categories, optional Sentinel activity logs, and selected source-table exports.
The source-table picker previews counts/latest timestamps; samples and incomplete
period exports are labeled explicitly. The report contains configuration details
and export links without the legacy findings/coverage dashboard.

Validation: 56 audit/backend tests and 7 audit UI tests passed using synthetic
responses. Checks include configuration-only requests, optional activity,
selected source queries, bounded sample labels, partial/denied responses, input
validation, tenant/workspace export paths, raw-record preservation, HTML/CSV
escaping, hashes, selection resets, and the source-table picker preview.
Tkinter multi-root teardown warnings remain; these assertions passed.

The full suite run before the final two UI tests ran 121 tests and reported 21
errors, all also present in an untouched HEAD snapshot (112 tests, 24 errors).
The remaining errors concern older onboarding/mode assumptions, exception types,
and Azure CLI discovery in the test environment. They are not new audit failures;
the full repository suite is not green.

No live tenant collection was performed. Follow the v3 acceptance steps in
AUDIT-EVIDENCE.md, including checking preview API product settings and table-plan
limitations, before relying on client exports.

## Desktop design refinement (2026-09-28)

Added a shared navy/teal theme, consistent typography and focus styling, responsive
card subtitles, a persistent workspace/tenant context line, grouped onboarding
forms, collapsible optional tools/log controls, a visible audit selection summary,
and a progress indicator shown only while work is running. The report styling and
desktop guide now match the interface. No deployment or permission logic changed.

Visually inspected the native desktop preview with empty local data, including
the compact audit and review layouts at 1120x740. Four new design tests verify
horizontal control bounds on all pages, disclosure state preservation, context
reset, and busy/read-only control states. These tests pass, along with seven audit
UI checks and 56 audit/backend checks (67 focused checks total).

The final full regression run executed 127 tests and retained the same 21 errors
already verified in the unchanged baseline. No new full-suite error was added.
Windows Tkinter multi-root teardown warnings remain. Live authentication and cloud
operations were not exercised during the cosmetic update.

## Analytics rules and reviewed Contributor assignment (2026-09-29)

Added automatic workspace rule inventory, enabled/disabled filters, search and
raw configuration details. Workspace changes clear the inventory and stale
responses are discarded. Missing state flags and incomplete collections remain
explicit. Added an access review that checks effective subscription permissions,
skips unnecessary grants, and allows an authorized signed-in user to assign
Contributor to itself after reviewing the exact identity and subscription.
Assignments persist until removed; this is not CyberQP timed activation.

Validation: 8 access/rule backend tests, 6 desktop design/state tests, 7 audit UI
tests and 56 audit backend tests passed (77 targeted tests). The audit picker test
now locates the dialog tree explicitly because the application has another tree.
Tkinter multi-root teardown warnings remain. Live Azure operations were not run;
previously documented full-suite baseline failures have not been resolved.

## Full-suite Lighthouse test migration (2026-09-29)

Resolved the 21 previously documented errors in the desktop test suite. Tests now
use the active Lighthouse exception class and configuration payloads. Retired
permissions-only behavior is checked for an explicit refusal before cloud calls;
replacement coverage checks Lighthouse preview wiring, configuration validation,
apply-lock cleanup, and destination/group-sensitive review fingerprints. Desktop
checks use the five current pages and verify sidebar navigation. Pending Tkinter
callbacks are cancelled before test windows are destroyed.

The complete offline command passed: python -m unittest discover -s tests -p
"test_*.py" (137 tests). No tests were skipped and the workflow still runs the full
suite. This supersedes the prior baseline-failure notes above. Live Azure access,
role assignment and client deployments have not been exercised by these tests.


## Lighthouse target length validation (2026-09-29)

Separated invalid-character and length diagnostics, derived the 45-character target
budget from the shared 19-character deployment prefix, and reused the same target
validator in desktop payloads and CLI configuration validation. Profile saving
uses the same component validator. No automatic folder or target renaming occurs.

All 146 offline tests passed, including nine added tests covering exact/over-limit
names, asymmetric component lengths, the reported client slug, invalid characters,
desktop save/payload errors, refusal before cloud calls, and deployment validate/
create using the identical bounded name. No live Azure deployment was performed.


## 64-character target support (2026-09-29)

The combined target now supports 64 characters including its hyphen separator;
each component supports 62. Azure deployment names retain the previous prefix
and full target when they fit. Longer names use a readable truncated target and
a stable 16-hex-character SHA-256 suffix, keeping the deployment name within 64.
Repository manifest targets, client paths and state retain the complete identity.

Boundary tests cover 64-character acceptance, 65-character rejection, the full
reported company slug with primary/workspace labels, unchanged short deployment
names, and stable distinct suffixes. A full run reached 149 tests with one new
manifest-test fixture error (missing discovered workspace); the fixture was fixed.
The initial full-suite rerun was blocked by two automatic permission-review
timeouts. A subsequent complete run, including the sign-in changes below, passed
all 155 tests. No live Azure operations were performed.


## Sign-in window foreground handoff (2026-09-29)

Added a Windows-only foreground helper for supported Microsoft broker and browser
sign-in windows. Automatic attempts are limited to newly opened/changed windows
for 90 seconds; successfully raised windows are not repeatedly focused. The
Bring sign-in window forward button remains available during authentication.
Completion and failure cancel the timer and release window metadata. Windows can
still deny focus, and browser matching depends on recognized English sign-in
titles. No topmost flag is applied to the browser or other applications.

All 155 offline tests passed. Foreground tests use mocked window APIs and do not
perform live authentication. Coverage includes existing-window exclusion, changed
browser titles, restoring minimized windows, focus denial, and success/failure
cleanup. Live Azure sign-in foreground behavior remains to be verified on the
user's Windows desktop.

## Reviewed client rule assignments (2026-10-02)

The catalog can prepare a multi-file client assignment review and publish one draft
PR through the existing GitHub CLI identity. Synthetic tests cover selected-file
isolation, override preservation, disabled targets, numeric quoted clients, BOM and
CRLF formatting, invalid effective rules, stale revisions and identity changes,
local journal failures, tampered saved requests, modified branches and recovery
after lost branch/PR responses. GUI tests cover filtering, hidden selections,
review invalidation, local/public/read-only catalog gates, saved review reopening
and minimum-size controls. Existing single-rule publishing tests exercise the
shared publisher alongside the new assignment tests.

These tests do not access the private repository or Azure. A private CI pilot and
an authorized disabled-rule deployment remain the live acceptance check.
