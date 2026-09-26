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
