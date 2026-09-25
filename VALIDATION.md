# Verification

The desktop and onboarding tests run entirely against simulated Azure/GitHub responses.
No client credentials or cloud deployments are required for the tests.

Checks cover discovery parsing, per-session configuration, preview/apply order, destination and identity conflicts, deletion confirmation and local-only effects, empty client inventory, resumed onboarding, and generated workspace filenames.

Run: python -m unittest discover -s tests -p "test_*.py" -v

The permissions-only Bash test harness uses an Azure CLI stub and can be run separately with Git Bash: bash tests/run-tests.sh.

Live Microsoft sign-in, tenant policy, Azure provider permissions and actual GitHub API behavior still require an authorized pilot. The private detection pipeline itself is outside this repository.
