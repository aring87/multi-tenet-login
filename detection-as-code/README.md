# Detection repository reference uploads

Place current, sanitized copies of private detection repository source files here.
These are reference inputs for app development; uploading them does not deploy rules,
update the private repository, or automatically change the application validator.
Preserve original paths when practical (for example, scripts/ and .github/workflows/).
Do not include tokens, credentials, real client inventories, or private rule content
that is not approved for this public repository.

The initial pipeline.py and rules.py copies belong in scripts/ in the detection
repository. They are not standalone app launchers in this folder. Python syntax is
checked by the offline tests without executing uploaded code.

## Numeric client names and the 64-character contract

The reviewed `pipeline.py` copy now accepts 1..64-character identifiers made of
lowercase letters or digits separated by single hyphens. Numeric client names
such as `413` and `0413` are supported. Folder/prefix agreement and all other
catalog checks still apply. The uploaded ZIP is unchanged historical reference.

**The private detection repository needs this fix too.** Updating the desktop app
alone cannot change the validator that runs in its GitHub Actions.

1. Save `numeric-client-slugs.patch` from this folder to your Downloads folder.
2. In Git Bash, change to your private detection-as-code checkout. Ensure your
   working changes are saved, then run:

   ```bash
   git switch main
   git pull --ff-only origin main
   git switch -c fix/numeric-client-slugs
   git apply --ignore-space-change --check ~/Downloads/numeric-client-slugs.patch
   git apply --ignore-space-change ~/Downloads/numeric-client-slugs.patch
   ```

   If `--check` fails, stop: your pipeline differs from the supplied snapshot.
   Compare the two small changes in the patch instead of replacing newer work.
   In GitHub's web editor you can make those same changes in `scripts/pipeline.py`.

3. For any already-created numeric client, edit its workspace YAML in the same
   branch. Keep identifiers as quoted strings, preserving leading zeros:

   ```yaml
   target: "413-workspace"
   client: "413"
   ```

   Keep the file under `clients/413/`. No tenant or subscription settings change.
   Newly onboarded clients are quoted correctly by the updated app. Do not
   coerce numeric YAML scalars after parsing: that can lose leading zeros.

4. Run `python scripts/pipeline.py check`, commit the pipeline and corrected
   client manifests, push the branch, and merge its pull request after checks
   pass. Re-run the failed workflow on the corrected revision. The existing
   dropdown automation can then read the numeric client normally.

Tests extract only the reviewed catalog validator and constants from this copy
to check numeric and malformed identifiers. Other uploaded code is not executed.
