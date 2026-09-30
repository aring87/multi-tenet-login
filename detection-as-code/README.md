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

Compatibility finding: this pipeline.py accepts client/target identifiers of 2..50
characters, while Lighthouse onboarding supports targets up to 64. Reconcile that
contract in the private pipeline before using targets longer than 50 characters.
The app's 64-character ARM deployment-name handling does not change that validator.
