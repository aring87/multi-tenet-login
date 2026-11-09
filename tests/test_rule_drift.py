"""Drift between the rule library and a workspace's live rules: offline, with in-memory fakes only."""
import json
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch
from sentinel_app import catalog_page, desktop_backend as backend, desktop_app as ui, rules_page
from sentinel_app.repository_catalog import catalog
from sentinel_app.rule_drift import DIFFERENT, LIVE_ONLY, MISSING, drift, match_targets
from test_desktop import SUB, TENANT, WORKSPACE, WS

A, B, C, D, E = (f"{c * 8}-1111-4111-8111-111111111111" for c in "abcde")
MANAGING = "99999999-9999-4999-8999-999999999999"


def rule_file(rule_id, name, severity="Medium", enabled=True, **extra):
    return json.dumps(dict(id=rule_id, name=name, severity=severity, enabled=enabled, **extra)).encode()


def target_file(target, azure, rules, enabled=True):
    client = target.split("-")[0]
    return json.dumps(dict(version=1, target=target, client=client, enabled=enabled, azure=azure, rules=rules)).encode()


def library(acme_enabled=True, acme_azure=None, extra=None):
    """Five rules: acme's target (the connected workspace, by workspace ID) is assigned A, B (raised to
    High), C and a file the library lacks; D is assigned only to bluestone, E to nobody."""
    files = {"rules/sentinel/a.yml": rule_file(A, "Alpha", "High", queryFrequency="PT1H"),
             "rules/sentinel/b.yml": rule_file(B, "Bravo", "Medium"),
             "rules/sentinel/c.yml": rule_file(C, "Charlie", "Low", enabled=False),
             "rules/sentinel/d.yml": rule_file(D, "Delta", "Low"),
             "rules/sentinel/e.yml": rule_file(E, "Echo", "Informational"),
             "clients/acme/acme-primary.yml": target_file(
                 "acme-primary", acme_azure or dict(tenant_id=MANAGING, workspace_id=WS.upper(), workspace_name="law-acme"),
                 [dict(path="rules/sentinel/a.yml"), dict(path="rules/sentinel/b.yml", overrides=dict(severity="High")),
                  dict(path="rules/sentinel/c.yml"), dict(path="rules/sentinel/gone.yml")], acme_enabled),
             "clients/bluestone/bluestone-primary.yml": target_file(
                 "bluestone-primary", dict(workspace_id="12121212-1212-4212-8212-121212121212", workspace_name="law-blue"),
                 [dict(path="rules/sentinel/d.yml")])}
    files.update(extra or {})
    return catalog(files, "synthetic", "one")


def live(rule_id, name, enabled=True, severity="Medium", kind="Scheduled", **props):
    return dict(name=rule_id, kind=kind, properties=dict(displayName=name, enabled=enabled, severity=severity, **props))


# A in step (its schedule differs, which drift does not flag), B still Medium (the override says High),
# C missing, D assigned elsewhere, and a built-in rule the library doesn't have.
RECORDS = [live(A.upper(), "Alpha", severity="High", queryFrequency="PT2H"), live(B, "Bravo", severity="Medium"),
           live(D, "Delta", severity="Low"), live("fusion-rule", "Advanced multistage attack", severity="High", kind="Fusion")]


class MatchTests(unittest.TestCase):
    def clients(self, *azures):
        return [dict(target=f"t{index}-primary", raw=dict(azure=azure)) for index, azure in enumerate(azures)]

    def test_workspace_id_decides_even_when_the_tenant_is_the_managing_one(self):
        clients = self.clients(dict(tenant_id=TENANT, workspace_name=WORKSPACE["workspace_name"]),
                               dict(tenant_id=MANAGING, workspace_id=WS.upper()))
        found, how = match_targets(clients, WORKSPACE)
        self.assertEqual(([client["target"] for client in found], how), (["t1-primary"], "workspace ID"))

    def test_destination_then_tenant_and_name_when_no_workspace_id_matches(self):
        destination = dict(subscription_id=SUB.upper(), resource_group="RG-Example-Sentinel",
                           workspace_name=WORKSPACE["workspace_name"])
        found, how = match_targets(self.clients(dict(destination, workspace_id="other")), WORKSPACE)
        self.assertEqual((len(found), how), (1, "subscription, resource group and workspace name"))
        found, how = match_targets(self.clients(dict(tenant_id=TENANT, workspace_name=WORKSPACE["workspace_name"])), WORKSPACE)
        self.assertEqual((len(found), how), (1, "tenant and workspace name"))

    def test_a_tenant_or_name_alone_never_matches(self):
        clients = self.clients(dict(tenant_id=TENANT), dict(workspace_name=WORKSPACE["workspace_name"]),
                               dict(tenant_id=TENANT, workspace_name="another-law"), dict())
        self.assertEqual(match_targets(clients, WORKSPACE), ([], None))


class DriftTests(unittest.TestCase):
    def test_missing_different_and_live_only_rules(self):
        result = drift(library(), RECORDS, WORKSPACE)
        self.assertEqual((result["status"], result["client"]["target"], result["how"]), ("ok", "acme-primary", "workspace ID"))
        rows = [(entry["kind"], entry["name"]) for entry in result["entries"]]
        self.assertEqual(rows, [(MISSING, "Charlie"), (DIFFERENT, "Bravo"),
                                (LIVE_ONLY, "Advanced multistage attack"), (LIVE_ONLY, "Delta")])
        different = result["entries"][1]
        self.assertEqual(different["differences"], [("Severity", "High", "Medium")])  # The target's override counts.
        self.assertEqual(different["overrides"], dict(severity="High"))
        # Delta is in the library (assigned elsewhere); the Fusion rule isn't in it at all.
        self.assertEqual([entry["rule"] is not None for entry in result["entries"][2:]], [False, True])
        self.assertEqual((result["in_sync"], result["assigned"]), (1, 3))  # Alpha: rule IDs match whatever their case.
        self.assertEqual(result["unavailable"], ["rules/sentinel/gone.yml"])

    def test_enabled_override_and_unset_values(self):
        files = {"rules/sentinel/f.yml": json.dumps(dict(id="f", name="Foxtrot", severity="High")).encode(),
                 "clients/acme/acme-primary.yml": target_file(
                     "acme-primary", dict(workspace_id=WS),
                     [dict(path="rules/sentinel/a.yml", overrides=dict(enabled=False)), dict(path="rules/sentinel/f.yml")])}
        snapshot = library(extra=files)
        records = [live(A, "Alpha", enabled=True, severity="high"), live("F", "Foxtrot", enabled=None, severity="High")]
        entries = drift(snapshot, records, WORKSPACE)["entries"]
        # The override switches Alpha off; severities compare without case. Foxtrot sets no enabled
        # value, so its workspace state isn't drift.
        self.assertEqual([(entry["name"], entry["differences"]) for entry in entries],
                         [("Alpha", [("State", "Disabled", "Enabled")])])

    def test_no_target_and_several_targets(self):
        self.assertEqual(drift(library(), RECORDS, dict(WORKSPACE, workspace_id="0" * 8))["status"], "no_target")
        twin = {"clients/twin/twin-primary.yml": target_file("twin-primary", dict(workspace_id=WS), [])}
        result = drift(library(extra=twin), RECORDS, WORKSPACE)
        self.assertEqual((result["status"], result["targets"]), ("ambiguous", ["acme-primary", "twin-primary"]))
        self.assertEqual(result["entries"], [])


class DriftPageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        # Nothing here may reach Azure or GitHub: the readers fail loudly if anything calls them.
        for p in (patch.object(ui, "DATA", self.data), patch.object(backend, "DATA", self.data),
                  patch.object(ui, "create_handoff", return_value=None),
                  patch.object(rules_page, "list_rules", side_effect=AssertionError("Azure read")),
                  patch.object(catalog_page.GitHubReader, "load", side_effect=AssertionError("GitHub read"))):
            p.start(); self.addCleanup(p.stop)
        self.start()

    def start(self, **settings):
        if settings: (self.data / "settings.json").write_text(json.dumps(settings))
        self.root = tk.Tk(); self.root.withdraw(); self.app = ui.App(self.root); self.root.update()
        self.page = self.app.drift_page
        self.addCleanup(self.close, self.root)

    def close(self, root):
        try:
            for timer in root.tk.call("after", "info"): root.after_cancel(timer)
            root.destroy()
        except tk.TclError:
            pass

    def connect(self, records=RECORDS, status="collected"):
        """Signed in to the sample workspace with ``records`` read from it, as the Workspace rules view does."""
        app = self.app; app.session = object(); app.workspace = dict(WORKSPACE)
        with patch.object(app, "work", side_effect=lambda title, task, done, **kw: done(dict(records=records, status=status))):
            app.rules_page.load()

    def load_library(self, snapshot=None):
        self.app.catalog_page.snapshot = snapshot or library(); self.app.catalog_page.render()

    def open(self):
        self.app.show_page("rules"); self.app.rule_view.set("drift"); self.root.update()

    def rows(self):
        return [self.page.tree.full_values(item) for item in self.page.tree.get_children()]

    def labels(self):
        return [self.page.filter_bar.items[value].cget("text") for value in self.page.filter_bar.order]

    def test_explains_what_it_needs_before_comparing(self):
        self.open()
        self.assertIn("Sign in and choose a workspace", self.page.empty.text.get())
        self.assertEqual(self.page.empty.button.cget("text"), "Go to Connect")
        self.assertEqual(str(self.page.refresh_button.cget("state")), "disabled")
        self.connect()
        self.assertIn("Load the rule library to compare it with law-example-sentinel", self.page.empty.text.get())
        self.assertEqual(self.page.empty.button.cget("text"), "Choose folder…")  # The library view's own way in.
        self.assertEqual(str(self.page.refresh_button.cget("state")), "normal")
        self.assertEqual(self.labels(), ["All", "Missing", "Different", "Only in workspace"])
        self.app.rules_page.loading = True; self.page.update()
        self.assertIn("Reading the rules from law-example-sentinel", self.page.empty.text.get())

    def test_lists_drift_for_the_connected_clients_target(self):
        self.connect(); self.open(); self.load_library()
        self.assertEqual(self.rows(), [
            ("Charlie", "Missing from workspace", "Disabled · Low", "—"),
            ("Bravo", "Severity differs", "Enabled · High", "Enabled · Medium"),
            ("Advanced multistage attack", "Not in library", "—", "Enabled · High"),
            ("Delta", "Not assigned here", "—", "Enabled · Low")])
        self.assertEqual(self.labels(), ["All 4", "Missing 1", "Different 1", "Only in workspace 2"])
        self.assertIn("acme-primary in the library, against law-example-sentinel", self.page.summary.get())
        self.assertIn("1 rule in step", self.page.summary.get())
        self.assertIn("1 rule assigned to acme-primary is not in the library", self.page.notice.get())
        self.page.filter.set(MISSING); self.assertEqual([row[0] for row in self.rows()], ["Charlie"])
        self.page.filter.set("all"); self.page.search.set("fusion"); self.page.render_soon.flush()
        self.assertEqual([row[0] for row in self.rows()], ["Advanced multistage attack"])  # The rule ID is searched too.
        self.page.search.set("zzz"); self.page.render_soon.flush()
        self.assertEqual(self.page.empty.text.get(), "No drift matches “zzz”.")
        self.page.empty.button.invoke(); self.assertEqual(len(self.rows()), 4)

    def test_a_drifted_rule_shows_both_sides(self):
        self.connect(); self.open(); self.load_library()
        self.page.tree.selection_set("1"); self.page.selected()
        self.assertEqual(self.page.detail_card.title_var.get(), "Bravo")
        self.assertIn("severity is Medium in the workspace, High in the library", self.page.explanation.get())
        compare = {self.page.compare.full_values(item)[0]: (self.page.compare.full_values(item)[1:],
                                                            "differs" in self.page.compare.item(item, "tags"))
                   for item in self.page.compare.get_children()}
        self.assertEqual(compare["Severity"], (("High", "Medium"), True))
        self.assertEqual(compare["State"], (("Enabled", "Enabled"), False))
        self.assertNotIn("Name", compare)  # The same on both sides, and already the title.
        facts = dict(zip((key.cget("text") for key in self.page.facts.keys), (value.full_text for value in self.page.facts.values)))
        self.assertEqual((facts["Rule ID"], facts["File"], facts["Target"], facts["Overrides"]),
                         (B, "rules/sentinel/b.yml", "acme-primary", 'severity: "High"'))
        self.assertEqual(json.loads(self.page.library_raw.text.get("1.0", "end"))["severity"], "High")  # With the override.
        self.assertEqual(json.loads(self.page.live_raw.text.get("1.0", "end"))["properties"]["severity"], "Medium")
        self.assertTrue(self.page.library_raw.row.winfo_manager() and self.page.live_raw.row.winfo_manager())
        # Both disclosures open in order, each JSON under its own toggle.
        self.page.live_raw.toggle(True); self.page.library_raw.toggle(True)
        packed = self.page.detail_area.inner.pack_slaves()
        order = [packed.index(part) for part in (self.page.library_raw.row, self.page.library_raw.frame,
                                                 self.page.live_raw.row, self.page.live_raw.frame)]
        self.assertEqual(order, sorted(order))
        # A missing rule has only the library side.
        self.page.tree.selection_set("0"); self.page.selected()
        self.assertIn("isn't in law-example-sentinel", self.page.explanation.get())
        self.assertFalse(self.page.live_raw.row.winfo_manager())
        self.assertEqual({self.page.compare.full_values(item)[2] for item in self.page.compare.get_children()}, {"—"})

    def test_comparing_again_keeps_the_selected_rule(self):
        self.connect(); self.open(); self.load_library()
        self.page.tree.selection_set("1"); self.page.selected()
        self.connect(RECORDS[::-1])  # Refresh: the same rules, read again.
        self.assertEqual(self.page.detail_card.title_var.get(), "Bravo")
        self.assertEqual(self.page.tree.full_values(self.page.tree.selection()[0])[0], "Bravo")
        fixed = [live(B, "Bravo", severity="High") if row["name"] == B else row for row in RECORDS]
        self.connect(fixed)  # Fixed in Sentinel: it leaves the list, and the details.
        self.assertNotIn("Bravo", [row[0] for row in self.rows()])
        self.assertEqual(self.page.detail_card.title_var.get(), "Drift details")

    def test_warns_when_the_comparison_is_incomplete(self):
        self.connect(status="error"); self.open(); self.load_library(library(acme_enabled=False))
        notice = self.page.notice.get()
        self.assertIn("acme-primary is switched off in the library", notice)
        self.assertIn("Not every rule could be read from law-example-sentinel", notice)
        self.assertTrue(self.page.banner.winfo_manager())
        self.connect(records=[], status="error")
        self.assertIn("The rules couldn't be read from law-example-sentinel", self.page.empty.text.get())
        self.assertFalse(self.page.banner.winfo_manager())

    def test_no_target_several_targets_and_no_drift(self):
        self.connect(); self.open()
        self.load_library(library(acme_azure=dict(workspace_name="law-acme")))
        self.assertIn("No client target in the rule library deploys to law-example-sentinel", self.page.empty.text.get())
        self.assertEqual(self.rows(), [])
        twin = {"clients/twin/twin-primary.yml": target_file("twin-primary", dict(workspace_id=WS), [])}
        self.load_library(library(extra=twin))
        self.assertIn("2 library targets deploy to law-example-sentinel: acme-primary, twin-primary", self.page.empty.text.get())
        only = {"clients/acme/acme-primary.yml": target_file("acme-primary", dict(workspace_id=WS), [dict(path="rules/sentinel/a.yml")])}
        self.connect([live(A, "Alpha", severity="High")]); self.load_library(library(extra=only))
        self.assertEqual(self.page.empty.text.get(),
                         "No drift. The 1 rule the library assigns to acme-primary match law-example-sentinel, "
                         "and law-example-sentinel has no other rules.")

    def test_reads_only_through_the_other_views(self):
        self.connect(); self.load_library()
        with patch.object(self.app, "work", side_effect=AssertionError("drift started a read")):
            self.open(); self.page.tree.selection_set("1"); self.page.selected(); self.root.update()
            self.page.filter.set(DIFFERENT); self.page.search.set("bravo"); self.page.render_soon.flush()
        with patch.object(self.app.rules_page, "load") as rules, patch.object(self.app.catalog_page, "load") as library_:
            self.app.refresh_current(); self.page.refresh_library()  # F5, and Refresh library.
        rules.assert_called_once_with(); library_.assert_called_once_with()

    def test_opening_drift_reads_the_saved_github_library_once(self):
        self.close(self.root); self.start(github_owner="example", github_repo="detections")
        self.connect()
        with patch.object(self.app.catalog_page, "load") as load:
            self.open(); self.root.update()
            load.assert_called_once_with()
            self.page.update(); self.root.update()  # Comparing again doesn't read it again.
            load.assert_called_once_with()

    def test_row_menu_opens_either_side(self):
        self.connect(); self.open(); self.load_library()
        entries = dict((label, command) for label, command, *rest in filter(None, self.page.row_menu("1")))
        entries["Show in Workspace rules"]()
        self.assertEqual(self.app.rule_view.get(), "workspace")
        self.assertEqual(self.app.rules_page.detail_card.title_var.get(), "Bravo")
        self.app.rule_view.set("drift"); self.root.update()
        dict((label, command) for label, command, *rest in filter(None, self.page.row_menu("1")))["Show in Rule library"]()
        self.assertEqual((self.app.rule_view.get(), self.app.catalog_page.view.get()), ("repository", "Rules"))
        self.assertEqual(self.app.catalog_page.detail_card.title_var.get(), "Bravo")
        missing = [entry for label, command, *entry in filter(None, self.page.row_menu("0"))
                   if label == "Show in Workspace rules"]
        self.assertEqual(missing, [[False]])  # Nothing live to show for a missing rule.

    def test_view_and_split_are_remembered_and_search_has_its_shortcut(self):
        self.connect(); self.root.deiconify(); self.root.geometry("1300x820"); self.open()
        self.app.focus_search(); self.root.update()
        self.assertEqual(str(self.root.focus_get()), str(self.page.search_entry))
        self.app.save_state()
        state = json.loads((self.data / "ui-state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["rule_view"], "drift"); self.assertIsInstance(state["drift_split"], float)
        self.close(self.root); self.start()
        self.assertEqual(self.app.rule_view.get(), "drift")


if __name__ == "__main__":
    unittest.main()
