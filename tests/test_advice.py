import unittest

from sentinel_app import advice
from sentinel_app.advice import first_sentence, friendly, lookup


class AdviceTests(unittest.TestCase):
    def test_rule_read_denial_names_the_workspace_and_the_role(self):
        message = ("AuthorizationFailed: The client 'a@example.com' does not have authorization to perform action "
                   "'Microsoft.SecurityInsights/alertRules/read' over scope '/subscriptions/x'")
        text = friendly(message, workspace="law-acme")
        self.assertTrue(text.startswith("Your account can't read analytics rules in law-acme."))
        self.assertIn("Microsoft Sentinel Reader", text); self.assertIn("CyberQP", text)
        self.assertIn("this workspace", friendly(message))  # Without context the advice still reads well.
        # An authorization failure on something else is not mistaken for the rule read.
        self.assertIsNone(lookup("AuthorizationFailed: roleAssignments/write"))

    def test_missing_repository_names_it(self):
        for message in ("GitHub read failed (HTTP 404): repository not found or no access.",
                        "Signed in as aring. GitHub read failed. Check the signed-in account, repository name and "
                        "organization access. For a private repository, Not Found can mean no access."):
            text = friendly(message, repo="isi-security/detection-as-code")
            self.assertTrue(text.startswith("GitHub couldn't find isi-security/detection-as-code for your account."))
            self.assertIn("Connect > Rule library", text)
        # Not signed in at all is a different problem, with different advice.
        self.assertIn("Sign in to GitHub", friendly("GitHub read failed. Run gh auth login"))

    def test_mfa_and_gcc_high_entries(self):
        for code in ("AADSTS50076", "AADSTS50079"):
            self.assertIn("multi-factor", friendly(code + ": Due to a configuration change..."))
            # The button above the banner may read "Sign in to <client>", so no label is named.
            self.assertNotIn("Sign in with Microsoft", friendly(code))
        self.assertEqual(friendly("Evidence collection supports Azure public cloud only."),
                         "Workspace rules can't be read from Azure Government (GCC High) workspaces yet.")

    def test_unknown_messages_pass_through_and_matching_ignores_case(self):
        self.assertEqual(friendly("Something unexpected"), "Something unexpected")
        self.assertIsNone(lookup("Something unexpected"))
        self.assertIn("write access", friendly("THIS GITHUB ACCOUNT NEEDS WRITE ACCESS"))
        self.assertIn("took too long", friendly("GitHub timed out. Refresh to try this read again."))
        self.assertIsNone(lookup("The operation timed out. A cloud change may still be running."))  # Azure, not GitHub.

    def test_sign_in_without_subscriptions_names_the_current_control(self):
        tip = lookup("Sign-in returned no accessible Azure subscriptions.\nTenant accounts: 1")
        self.assertTrue(tip.startswith("You're signed in"))
        self.assertIn("Can't see a subscription?", tip); self.assertNotIn("Check missing subscription", tip)

    def test_github_cli_and_sign_in_failures(self):
        for message in ("GitHub CLI isn't installed on this PC. Ask your administrator to install it.",
                        "GitHub CLI is not installed. Install it first."):
            self.assertEqual(lookup(message), "GitHub CLI is not installed on this PC. Ask your administrator to install it.")
        self.assertIn("Sign in to GitHub", lookup("GitHub sign-in didn't finish. Check the one-time code shown in Session activity."))

    def test_a_moved_library_rule_points_at_where_it_will_be_saved(self):
        tip = lookup("The original repository rule is missing or the destination changed. Start a new draft for a new rule.")
        self.assertIn("Where it will be saved", tip)
        self.assertIn("Library ⋯", lookup("The catalog is incomplete or invalid"))

    def test_review_dialog_keeps_its_old_import(self):
        from sentinel_app import rule_review_dialog
        self.assertIs(rule_review_dialog.FRIENDLY, advice.FRIENDLY)
        self.assertIs(rule_review_dialog.friendly, advice.friendly)

    def test_first_sentence_for_the_status_bar(self):
        self.assertEqual(first_sentence("One thing. Then another."), "One thing.")
        self.assertEqual(first_sentence("\nFirst line\nsecond line"), "First line")
        self.assertEqual(first_sentence("Version 2.1.0 is out. More."), "Version 2.1.0 is out.")
        self.assertEqual(first_sentence(""), "")


if __name__ == "__main__":
    unittest.main()
