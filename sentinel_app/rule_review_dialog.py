"""Submit a validated rule for a teammate's approval, in plain language.

The window checks the rule library, shows exactly what will change, and only
writes to GitHub when the analyst selects Submit for approval.
"""
import copy
import json
import re
import tkinter as tk
from tkinter import ttk
import webbrowser

from . import ui_kit as kit
from .advice import FRIENDLY, friendly  # noqa: F401  (FRIENDLY kept for older imports)
from .desktop_theme import COLORS, FONTS, px
from .repository_reviews import ReviewService, diff
from .rule_drafts import validated_yaml
from .rules_page import CopyValues

STAGES = ("Check", "Submit", "Teammate approves", "Deployed")
PULL_URL = re.compile(r"https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/pull/(\d+)")


def pull_reference(url):
    """'https://github.com/org/repo/pull/128' -> 'pull request #128 in org/repo'."""
    match = PULL_URL.fullmatch(url or "")
    return f"pull request #{match.group(2)} in {match.group(1)}" if match else "the pull request"


def new_rule_path(form):
    """Where a new rule goes in the library: the folder of its first MITRE tactic (CommandAndControl ->
    command-and-control/), or uncategorized/ without one, as the library's README files rules."""
    filename = re.sub(r"[^a-z0-9]+", "-", form["name"].lower()).strip("-")[:80] or "new-rule"
    first = next((t.strip() for t in str(form.get("tactics", "")).split(",") if t.strip()), "")
    folder = re.sub(r"(?<!^)(?=[A-Z])", "-", first).lower() if re.fullmatch(r"[A-Za-z]+", first) else "uncategorized"
    return "rules/sentinel/" + folder + "/" + filename + ".yml"


class ReviewDialog:
    def __init__(self, builder, record=None, auto=False):
        self.builder, self.app = builder, builder.app
        self.record = record
        self.form = copy.deepcopy(builder.form())
        self.source = copy.deepcopy(builder.source)
        self.uploaded = getattr(builder, "source_uploaded", False) and record is None
        self.service = ReviewService()
        self.folder = builder.data_dir / "review-requests"
        repository = (self.source or {}).get("repository") or self.app.github_repository()
        self.repository = tk.StringVar(value=record["plan"]["repository"] if record else repository.strip("/"))
        self.path = tk.StringVar(value=record["plan"]["path"] if record else
                                 (self.source or {}).get("path", new_rule_path(self.form)))
        self.controls = []

        window = self.window = tk.Toplevel(self.app.root)
        window.title("Submit for approval")
        window.configure(bg=COLORS["card"])
        body = ttk.Frame(window, style="Card.TFrame", padding=(kit.space("xl"), px(20), kit.space("xl"), px(18)))
        body.pack(fill="both", expand=True)

        head = ttk.Frame(body, style="Card.TFrame"); head.pack(fill="x")
        updating = bool(record["plan"]["before"]) if record else bool(self.source)
        self.kind_pill = kit.Pill(head, "Update to an existing rule" if updating else "New rule", "info")
        self.kind_pill.pack(side="right", anchor="n")
        ttk.Label(head, text="Submit for approval", style="CardTitle.TLabel").pack(anchor="w")
        name = record["plan"]["name"] if record else self.form["name"]
        ttk.Label(head, text=name or "Untitled rule", style="CardSubtitle.TLabel").pack(anchor="w", pady=(px(2), 0))
        self.progress = kit.ProgressLine(body)
        stages = ttk.Frame(body, style="Card.TFrame"); stages.pack(fill="x", pady=(px(14), px(12)))
        self.stage_labels = []
        for index, stage in enumerate(STAGES):
            if index:
                tk.Label(stages, text="→", font=FONTS["small"], bg=COLORS["card"], fg=COLORS["muted"]).pack(side="left", padx=px(8))
            label = tk.Label(stages, text=f"{index + 1}  {stage}", font=FONTS["strong_small"], bg=COLORS["card"], bd=0)
            label.pack(side="left"); self.stage_labels.append(label)

        self.notice = tk.StringVar()
        self.banner = kit.Banner(body, self.notice); self.banner.pack(fill="x", pady=(0, px(14)))
        self.summary = CopyValues(body, key_width=16, copied=lambda key: self.app.flash(key + " copied."))
        self.summary.pack(fill="x")

        self.actions, buttons = kit.action_row(body, primary=("Submit for approval", self.submit),
                                               secondary=(("Check", self.prepare),), close=("Close", self.close),
                                               aux=(("Open in GitHub", self.open_link),))
        self.actions.pack(side="bottom", fill="x", pady=(px(14), 0))
        self.submit_button, self.prepare_button, self.close_button, self.link_button = buttons

        where = ttk.Frame(body, style="Card.TFrame"); where.pack(side="bottom", fill="x", pady=(px(10), 0))
        self.where_body = ttk.Frame(where, style="Card.TFrame", padding=(0, px(8), 0, 0))
        self.where_button = ttk.Button(where, text="+  Where it will be saved", style="Disclosure.TButton",
                                       command=self.toggle_where)
        self.where_button.pack(fill="x")
        for label, variable, hint in (("Rule library", self.repository, "GitHub repository, as organization/name"),
                                      ("Rule file", self.path, "under rules/sentinel/, lowercase, no spaces")):
            kit.field_label(self.where_body, label, hint)
            entry = ttk.Entry(self.where_body, textvariable=variable); entry.pack(fill="x", pady=(0, px(10)))
            self.controls.append(entry)

        # The hint and the legend sit beside their headings, so the comparison keeps its room in a
        # window clamped to a small screen (150% on 1080p).
        why = kit.section_label(body, "Why is this changing?"); why.pack_configure(pady=(px(14), px(6)))
        ttk.Label(why, text="Your teammate reads this when approving. One or two sentences is enough.",
                  style="Hint.TLabel").pack(side="right", padx=(px(12), 0))
        frame, self.reason = kit.text_area(body, 3, font="body")
        frame.pack(fill="x")
        self.controls.append(self.reason)
        if record and record.get("note"): kit.set_text(self.reason, record["note"])

        legend = kit.section_label(body, "What will change"); legend.pack_configure(pady=(px(14), px(6)))
        for text, color in (("Removed lines", COLORS["danger"]), ("Added lines", COLORS["success"])):
            tk.Label(legend, text="■ " + text, font=FONTS["small"], bg=COLORS["card"], fg=color).pack(side="right", padx=(px(14), 0))
        frame, self.text = kit.text_area(body, 14, readonly=True, wrap="none",
                                         placeholder="The comparison with the rule library appears here after Check.")
        frame.pack(fill="both", expand=True)
        where.lift(); self.actions.lift()  # Packed first to keep their room; Tab reaches them last, as they read.
        for tag, fg, bg in (("added", COLORS["success"], COLORS["success_soft"]),
                            ("removed", COLORS["danger"], COLORS["danger_soft"]),
                            ("meta", COLORS["muted"], None), ("hunk", COLORS["accent"], None)):
            self.text.tag_configure(tag, foreground=fg, **({"background": bg} if bg else {}))

        self.locked = record is not None
        self.repository.trace_add("write", self.invalidate); self.path.trace_add("write", self.invalidate)
        kit.place_dialog(window, self.app.root, 900, 780, 720, 620)
        kit.modal(window, self.app.root, self.close, primary=self.prepare_button, focus=self.reason)
        if record:
            self.render()
        else:
            self.refresh_controls(); self.stage(0)
            self.say("Select Check to compare this rule with the rule library. Nothing is sent until you submit.")
            self.summary.set(self.summary_rows())
            if auto: window.after(50, self.prepare)

    # ------------------------------------------------------------ helpers
    def stage(self, current):
        for index, label in enumerate(self.stage_labels):
            label.configure(fg=COLORS["accent"] if index == current else COLORS["ink_2"] if index < current else COLORS["muted"],
                            font=FONTS["strong_small"] if index <= current else FONTS["small"])

    def toggle_where(self, show=None):
        visible = bool(self.where_body.winfo_manager())
        show = (not visible) if show is None else show
        if show and not visible: self.where_body.pack(fill="x")
        elif not show and visible: self.where_body.pack_forget()
        self.where_button.configure(text=("−  " if show else "+  ") + "Where it will be saved")

    def summary_rows(self, plan=None):
        rows = [("Library", (plan or {}).get("repository") or self.repository.get().strip() or "Not set — open Where it will be saved"),
                ("Rule file", (plan or {}).get("path") or self.path.get().strip())]
        if plan:
            rows.insert(1, ("Submitting as", plan["identity"] + " (GitHub)"))
            impact = plan["impact"]
            rows.append(("Used by clients", "Not assigned to any client yet" if not impact else
                         f"{len(impact)}: " + ", ".join(t["target"] for t in impact[:6]) + (" …" if len(impact) > 6 else "")))
        return rows

    def say(self, text, tone="info"):
        self.notice.set(text); self.banner.tone(tone)

    def close(self):
        if self.app.busy:
            self.say("Wait for the current step to finish, then select Close.", "warning"); return
        self.window.destroy()

    def invalidate(self, *args):
        self.record = None; self.refresh_controls()
        self.show("")
        self.summary.set(self.summary_rows()); self.stage(0)
        self.say("Destination changed. Select Check to compare again.")

    def show(self, text, header=""):
        """Show ``header`` in muted text, then ``text`` as a unified diff coloured by line."""
        self.text.configure(state="normal"); self.text.delete("1.0", "end")
        self.text.insert("end", header, "meta")
        for line in text.splitlines(keepends=True):
            tag = ("meta" if line.startswith(("---", "+++")) else "hunk" if line.startswith("@@")
                   else "added" if line.startswith("+") else "removed" if line.startswith("-") else ())
            self.text.insert("end", line, tag)
        self.text.configure(state="disabled")
        kit.refresh_placeholder(self.text)

    def refresh_controls(self):
        for widget in self.controls: widget.configure(state="disabled" if self.locked else "normal")
        self.prepare_button.configure(state="disabled" if self.locked else "normal",
                                      text="Check again" if self.record else "Check")
        submittable = bool(self.record and not self.record.get("pull_request"))
        self.submit_button.configure(state="normal" if submittable else "disabled")
        self.link_button.configure(state="normal" if self.record and self.record.get("pull_request") else "disabled")
        self.close_button.configure(state="normal")
        # The blue button is the next step: Check, then Submit for approval; none once submitted.
        lead = self.submit_button if submittable else None if self.locked else self.prepare_button
        kit.promote(self.window, lead, *(b for b in (self.submit_button, self.prepare_button) if b is not lead))

    def run(self, title, task, done):
        def on_error(error):
            advice = friendly(error)
            if self.locked:
                advice += (" Your request is saved; select Submit for approval again to finish it. "
                           "Recovery files are in " + str(self.folder) + ".")
            self.say(advice, "danger")
            if "Where it will be saved" in advice or "file is already" in advice: self.toggle_where(True)
        kit.run_in_dialog(self.app, self.window, title, task, done,
                          controls=self.controls + [self.prepare_button, self.submit_button, self.link_button, self.close_button],
                          say=self.say, page="builder", on_error=on_error, progress=self.progress, after=self.refresh_controls,
                          log_as="Rule submission:")

    # ------------------------------------------------------------ actions
    def prepare(self):
        if self.app.busy or self.locked: return
        self.record = None
        self.show("")
        repository, path = self.repository.get().strip(), self.path.get().strip()
        if not repository:
            self.toggle_where(True)
            self.say("Enter the rule library under Where it will be saved, or set it once on Connect > Rule library.", "warning")
            return
        progress = self.app.file_progress(lambda text: self.window.winfo_exists() and self.say(text))
        def task():
            content, _ = validated_yaml(self.form)
            return self.service.prepare(repository, path, content, self.form["allow_missing_mitre"], self.source, progress)
        def done(record): self.record = record; self.render()
        self.stage(0)
        self.run("Comparing the rule with the rule library. Nothing is sent to GitHub yet…", task, done)

    def render(self):
        plan = self.record["plan"]
        self.summary.set(self.summary_rows(plan))
        lines = []
        if plan["impact"]:
            lines.append("Clients that use this rule (from the library configuration):")
            for target in plan["impact"]:
                lines.append("  " + target["target"] + " — " + ("enabled" if target["rule_enabled"] else "disabled") +
                             (" · overrides " + json.dumps(target["overrides"]) if target["overrides"] else ""))
            lines += ["", ""]
        self.show(diff(plan), "\n".join(lines))
        if self.record.get("pull_request"):
            self.stage(2)
            self.say("Submitted as " + pull_reference(self.record["pull_request"]) + ". A teammate is notified and reviews "
                     "it in GitHub; nothing changes in any workspace until they approve it. Select Open in GitHub to follow along.",
                     "success")
        else:
            self.stage(1)
            notes = list(plan["warnings"])
            if self.uploaded:
                notes.insert(0, "this update came from an opened file, so check the red lines: they will be "
                                "removed from the library version")
            if notes:
                self.say("Ready to submit — please check first: " + "; ".join(notes) + ".", "warning")
            else:
                self.say("Ready to submit. Check the changes below, say why the rule is changing, then select "
                         "Submit for approval. A teammate reviews it before anything is deployed.", "success")
        self.refresh_controls()

    def submit(self):
        if self.app.busy or not self.record or self.record.get("pull_request"): return
        note = self.reason.get("1.0", "end-1c").strip()
        if not note:
            self.say("Say why the rule is changing — your teammate reads it when approving.", "warning")
            self.reason.focus_set(); return
        self.record["note"] = note
        self.locked = True
        def done(url):
            self.render()
            self.builder.submitted(url)
        self.stage(1)
        self.run("Submitting for approval…", lambda: self.service.submit(self.record, self.folder), done)

    def open_link(self):
        if self.record and self.record.get("pull_request"):
            url = self.record["pull_request"]
            if PULL_URL.fullmatch(url):
                webbrowser.open(url)
