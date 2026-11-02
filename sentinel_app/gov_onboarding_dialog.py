"""Preview, then apply, GCC High onboarding for the workspace selected on Connect."""
import re
import tkinter as tk
from tkinter import ttk
import webbrowser

from . import ui_kit as kit
from .desktop_backend import gov_config_from_workspace, gov_run
from .desktop_theme import COLORS, px
from .gov_onboarding import DEFAULT_LABEL

DEFAULT_RULE = "rules/sentinel/testing/pipeline-connection-test.yml"
PR_LINK = re.compile(r"Review pull request: (https://github\.com/\S+)")


def suggested_slug(name):
    """A client folder name from the saved client name, e.g. 'AOSense, Inc' -> 'aosense'."""
    words = re.sub(r"\b(inc|llc|corp|corporation|co|ltd|incorporated)\b\.?", " ", (name or "").lower())
    return re.sub(r"[^a-z0-9]+", "-", words).strip("-")[:40].strip("-")


class GovOnboardingDialog:
    def __init__(self, app):
        self.app = app
        self.workspace = dict(app.workspace)
        self.reviewed = None
        self.pull_request = None
        self.window = tk.Toplevel(app.root)
        self.window.title("Onboard GCC High client"); self.window.configure(bg=COLORS["card"])
        body = ttk.Frame(self.window, style="Card.TFrame", padding=(kit.space("xl"), px(20), kit.space("xl"), px(18)))
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Onboard GCC High client", style="CardTitle.TLabel").pack(anchor="w")
        kit.wrap_to_parent(ttk.Label(body, text=(
            "Creates the preview and deploy pipeline app registrations in this client's Azure Government tenant, "
            "adds their GitHub trust, grants their roles on the workspace resource group, and opens the target pull "
            "request with the tenant ID and client IDs filled in. Preview first; nothing changes until you apply."),
            style="CardSubtitle.TLabel", justify="left", width=1), minimum=px(200)).pack(fill="x", pady=(px(2), px(14)))

        # Bottom-anchored controls are packed first so they always keep their room.
        self.actions, buttons = kit.action_row(body, primary=("Apply reviewed setup", self.apply),
                                               secondary=(("Preview", self.preview), ("Open pull request", self.open_pr)),
                                               close=("Close", self.close))
        self.actions.pack(side="bottom", fill="x", pady=(px(14), 0))
        self.apply_button, self.preview_button, self.pr_button, self.close_button = buttons
        self.buttons = tuple(buttons)
        self.notice = tk.StringVar(value="Check the client folder name, then select Preview.")
        self.banner = kit.Banner(body, self.notice); self.banner.pack(side="bottom", fill="x", pady=(px(10), 0))

        details = kit.KeyValues(body)
        details.set([["Tenant", self.workspace["tenant_id"]], ["Subscription", self.workspace["subscription_id"]],
                     ["Resource group", self.workspace["resource_group"]], ["Workspace", self.workspace["workspace_name"]]])
        details.pack(fill="x", pady=(0, px(12)))

        self.vars = {}
        row = ttk.Frame(body, style="Card.TFrame"); row.pack(fill="x")
        row.columnconfigure((0, 1), weight=1, uniform="gov")
        for column, (key, label, initial, hint) in enumerate((
                ("client", "Client folder", suggested_slug(app.vars["client_name"].get()),
                 "Lowercase letters, digits and hyphens. Becomes clients/<folder>/."),
                ("label", "Workspace label", DEFAULT_LABEL, "The target is <folder>-<label>."))):
            box = ttk.Frame(row, style="Card.TFrame")
            box.grid(row=0, column=column, sticky="new", padx=(0, px(12)) if column == 0 else 0)
            self.field(box, key, label, initial, hint)
        self.field(body, "rule", "Initial rule (optional)", DEFAULT_RULE,
                   "Committed disabled, so the target is selectable in the pipeline. Leave blank to add the target disabled.")
        for var in self.vars.values():
            var.trace_add("write", lambda *args: self.changed())

        review_frame, self.review = kit.text_area(body, 12, readonly=True,
                                                  placeholder="The plan appears here after Preview. Nothing changes until you apply it.")
        review_frame.pack(fill="both", expand=True)
        kit.place_dialog(self.window, app.root, 900, 760, 760, 600)
        kit.modal(self.window, app.root, self.close)
        self.controls()

    def field(self, parent, key, label, initial, hint):
        kit.field_label(parent, label)
        self.vars[key] = tk.StringVar(value=initial)
        ttk.Entry(parent, textvariable=self.vars[key]).pack(fill="x")
        # width=1: the hint wraps to the column it is given instead of widening it.
        kit.wrap_to_parent(ttk.Label(parent, text=hint, style="Hint.TLabel", justify="left", wraplength=px(380), width=1),
                           minimum=px(160)).pack(fill="x", pady=(px(4), px(12)))

    def config(self):
        values = {key: var.get().strip() for key, var in self.vars.items()}
        return gov_config_from_workspace(self.workspace, values["client"], values["label"], self.app.settings,
                                         rule_path=values["rule"])

    def say(self, text, tone="info"):
        self.notice.set(text); self.banner.tone(tone)

    def changed(self):
        if self.reviewed is not None:
            self.reviewed = None
            self.say("Settings changed. Preview again before applying.", "warning")
        self.controls()

    def controls(self):
        self.preview_button.configure(state="normal")
        self.apply_button.configure(state="normal" if self.reviewed else "disabled")
        self.pr_button.configure(state="normal" if self.pull_request else "disabled")
        self.close_button.configure(state="normal")

    def show(self, text):
        kit.set_text(self.review, text)

    def run(self, apply):
        try:
            config = self.config()
        except Exception as error:
            self.say(str(error), "danger"); return
        if apply and config != self.reviewed:
            self.say("Preview these settings before applying.", "warning"); return
        session = self.app.session
        def done(result):
            summary, output = result
            self.show(output)
            match = PR_LINK.search(output)
            if match: self.pull_request = match.group(1)
            if apply:
                self.reviewed = None
                self.say(summary + " Merge it, then run preview for the new target on main.", "success")
                self.app.flash("GCC High onboarding applied.")
            else:
                self.reviewed = config
                self.say(summary + " Review the plan, then select Apply reviewed setup.", "success")
        def failed(error):
            self.reviewed = None
            self.show(error)
            self.say("Stopped. The reason is at the end of the plan below.", "danger")
        kit.run_in_dialog(self.app, self.window,
                          "Applying GCC High onboarding…" if apply else "Checking the GCC High tenant and repository…",
                          lambda: gov_run(session, config, apply), done, controls=self.buttons, say=self.say,
                          page="connect", on_error=failed, after=self.controls, log_as="GCC High onboarding:")

    def preview(self):
        self.run(False)

    def apply(self):
        if self.reviewed: self.run(True)

    def open_pr(self):
        if self.pull_request: webbrowser.open(self.pull_request)

    def close(self):
        if self.app.busy:
            self.say("Wait for the current step to finish, then select Close.", "warning"); return
        self.window.destroy()
