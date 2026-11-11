"""The Query box as an editor (kql_editor): its colours and themes, the completion list and its keys,
and the suggested spellings. Everything runs on a local Tk window; nothing calls Azure."""
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from sentinel_app import desktop_app as ui
from sentinel_app import kql_assist as assist
from sentinel_app import kql_editor
from sentinel_app import ui_kit as kit
from sentinel_app.desktop_theme import KQL_THEME, KQL_THEMES, configure_theme
from sentinel_app.kql_editor import KqlEditor, fix_label
from test_ui_kit import contrast

SLIPS = 'SignLogs\n| wher ResultType != "0" && Name contians "admin"\n| summarize Attempts = Count() by tostrng(IPAddress)'
FIXED = 'SigninLogs\n| where ResultType != "0" and Name contains "admin"\n| summarize Attempts = count() by tostring(IPAddress)'
KEYS = {" ": "space", "|": "bar", "(": "parenleft", ")": "parenright", "=": "equal", "\n": "Return", "-": "minus",
        ">": "greater", '"': "quotedbl", ".": "period", ",": "comma", "_": "underscore"}


class ThemeTests(unittest.TestCase):
    def test_every_theme_has_every_colour(self):
        self.assertIn(KQL_THEME, KQL_THEMES)
        self.assertEqual(next(iter(KQL_THEMES)), KQL_THEME)  # The default is listed first.
        keys = set(KQL_THEMES[KQL_THEME])
        self.assertTrue(set(assist.KINDS) | {"column", "background", "ink", "muted", "popup"} <= keys)
        for name, theme in KQL_THEMES.items():
            with self.subTest(theme=name):
                self.assertEqual(set(theme), keys)
                self.assertTrue(all(len(value) == 7 and value.startswith("#") for value in theme.values()))

    def test_every_text_colour_is_readable_where_it_is_drawn(self):
        for name, theme in KQL_THEMES.items():
            for kind in assist.KINDS + ("column", "ink", "muted"):
                with self.subTest(theme=name, kind=kind):
                    for surface in ("background", "popup"):
                        self.assertGreaterEqual(contrast(theme[kind], theme[surface]), 4.5, surface)
                    # Under a mark, in a selection and on the list's chosen row it must still read.
                    for surface in ("error_soft", "warning_soft", "selection", "popup_selected"):
                        self.assertGreaterEqual(contrast(theme[kind], theme[surface]), 3, surface)
            with self.subTest(theme=name):
                for mark in ("error", "warning"):  # The underline against the box and its own soft background.
                    self.assertGreaterEqual(contrast(theme[mark], theme["background"]), 3, mark)
                    self.assertGreaterEqual(contrast(theme[mark], theme[mark + "_soft"]), 3, mark)
                self.assertGreaterEqual(contrast(theme["caret"], theme["background"]), 4.5)
                # Each part of a query has a colour of its own (a let name may share its table's hue, not its shade).
                self.assertEqual(len({theme[kind] for kind in assist.KINDS}), len(assist.KINDS))


class EditorTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk(); configure_theme(self.root); self.root.geometry("900x700+40+40")
        self.addCleanup(self.cleanup)
        frame, self.text = kit.text_area(self.root, 7, undo=True, placeholder="A query")
        frame.pack(fill="x", padx=20, pady=20)
        self.editor = KqlEditor(self.text)
        self.root.update()

    def cleanup(self):
        for timer in self.root.tk.call("after", "info"): self.root.after_cancel(timer)
        self.root.destroy()

    def put(self, query):
        self.text.delete("1.0", "end"); self.text.insert("1.0", query); self.text.mark_set("insert", "end-1c")
        self.text.edit_reset()
        return self.editor.check()

    def query(self):
        return self.text.get("1.0", "end-1c")

    def focus(self):
        self.root.deiconify(); self.root.update()
        self.text.focus_force(); self.root.update()  # Key events go to the focused widget.

    def press(self, keysym):
        self.text.event_generate("<KeyPress>", keysym=keysym); self.root.update()

    def type(self, characters):
        for character in characters: self.press(KEYS.get(character, character))

    def listed(self, count=3):
        offer = self.editor.offer
        return None if offer is None else [choice.text for choice in offer.choices[:count]]

    def tagged(self, tag):
        ranges = [str(index) for index in self.text.tag_ranges(tag)]
        return [self.text.get(start, end) for start, end in zip(ranges[::2], ranges[1::2])]


class ColourTests(EditorTestCase):
    def test_checking_colours_each_part_and_marks_each_problem(self):
        self.put('SignLogs // sign-ins\n| where ResultType != "0" and TimeGenerated > ago(1h)\n| summarize count() by User')
        self.assertEqual(self.tagged("syntax-table"), ["SignLogs"])
        self.assertEqual(self.tagged("syntax-comment"), ["// sign-ins"])
        self.assertEqual(self.tagged("syntax-step"), ["|", "where", "|", "summarize"])
        self.assertEqual(self.tagged("syntax-string"), ['"0"'])
        self.assertEqual(self.tagged("syntax-function"), ["ago", "count"])
        self.assertEqual(self.tagged("syntax-keyword"), ["and", "by"])
        self.assertEqual(self.tagged("syntax-number"), ["1h"])
        self.assertEqual(self.tagged("kql-warning"), ["SignLogs"])
        self.assertEqual(self.tagged("kql-error"), [])
        # The marks are drawn over the colours, and a selection over both.
        order = self.text.tag_names()
        self.assertLess(order.index("syntax-table"), order.index("kql-warning"))
        self.assertLess(order.index("kql-warning"), order.index("kql-error"))
        self.assertEqual(order[-1], "sel")

    def test_painting_alone_leaves_the_marks_for_the_check(self):
        self.put("SignLogs | take 1")
        self.text.insert("end", " | wher x"); self.editor.paint()
        self.assertEqual(self.tagged("syntax-step"), ["|", "take", "|"])  # Coloured at once.
        self.assertEqual(self.tagged("kql-warning"), ["SignLogs"])       # Marked at the next check.
        self.editor.check()
        self.assertEqual(self.tagged("kql-warning"), ["SignLogs", "wher"])
        self.assertIsNone(self.put("")); self.assertIsNone(self.editor.report)
        self.assertEqual([tag for tag in kql_editor.SYNTAX + kql_editor.MARKS if self.text.tag_ranges(tag)], [])

    def test_a_theme_recolours_the_box_and_an_unknown_one_is_the_default(self):
        self.assertEqual(self.editor.theme, KQL_THEME)
        self.put("SigninLogs | take 1")
        for name, theme in KQL_THEMES.items():
            with self.subTest(theme=name):
                self.editor.set_theme(name)
                text = self.text
                self.assertEqual(str(text.cget("bg")).lower(), theme["background"].lower())
                self.assertEqual(str(text.cget("fg")).lower(), theme["ink"].lower())
                self.assertEqual(str(text.cget("insertbackground")).lower(), theme["caret"].lower())
                self.assertEqual(str(text.tag_cget("syntax-step", "foreground")).lower(), theme["step"].lower())
                self.assertEqual(str(text.tag_cget("kql-error", "background")).lower(), theme["error_soft"].lower())
                self.assertEqual(str(text.placeholder_label.cget("bg")).lower(), theme["background"].lower())
                self.assertEqual(self.tagged("syntax-table"), ["SigninLogs"])  # The colours stay on the query.
        self.editor.set_theme("Not a theme"); self.assertEqual(self.editor.theme, KQL_THEME)
        self.assertEqual(kql_editor.theme_name(None), KQL_THEME)

    def test_coloured_text_keeps_the_line_height(self):
        """Bold steps and italic comments must not make a line taller: the builder counts on the box's height."""
        metrics = lambda font: self.text.tk.call("font", "metrics", font, "-linespace")
        plain = metrics(self.text.cget("font"))
        for tag in kql_editor.SYNTAX:
            self.assertEqual(metrics(self.text.tag_cget(tag, "font")), plain, tag)


class CompletionTests(EditorTestCase):
    def test_typing_opens_the_list_and_tab_inserts(self):
        self.focus()
        self.type("S"); self.assertIsNone(self.editor.offer)  # One letter of a table isn't enough to guess from.
        self.type("ig")
        self.assertEqual(self.listed(1), ["SigninLogs"])
        self.assertTrue(self.editor.popup.winfo_ismapped())
        self.press("Tab")
        self.assertEqual(self.query(), "SigninLogs")
        self.assertIsNone(self.editor.offer); self.assertFalse(self.editor.popup.winfo_ismapped())
        self.press("Tab")  # With the list closed, Tab indents as before.
        self.assertEqual(self.query(), "SigninLogs    ")

    def test_steps_are_listed_straight_after_a_pipe_and_enter_completes_one(self):
        self.put("SigninLogs\n"); self.focus()
        self.type("| ")
        self.assertEqual(self.listed(), ["where", "summarize", "project"])
        self.type("su"); self.assertEqual(self.listed(), ["summarize"])
        self.press("Return")
        self.assertEqual(self.query(), "SigninLogs\n| summarize ")  # Completed, with its space and no new line.
        self.type("c"); self.assertIsNone(self.editor.offer)
        self.type("o"); self.assertEqual(self.listed(2), ["count", "countif"])
        self.press("Tab")
        self.assertEqual(self.query(), "SigninLogs\n| summarize count()")
        self.assertEqual(self.text.index("insert"), self.text.index("end-1c"))  # count() takes nothing: caret after it.

    def test_enter_ends_the_line_unless_a_choice_was_made(self):
        self.focus()
        self.put("SigninLogs\n| where Us"); self.type("e")
        self.assertEqual(self.listed(1), ["UserPrincipalName"])
        self.press("Return")
        self.assertEqual(self.query(), "SigninLogs\n| where Use\n")  # A new line; the word was not replaced.
        self.assertIsNone(self.editor.offer)
        self.put("SigninLogs\n| where Us"); self.type("e")
        self.press("Down"); self.press("Return")  # Chosen with the arrows: Enter inserts it.
        self.assertEqual(self.query(), "SigninLogs\n| where UserDisplayName")
        self.put("SigninLogs\n| projec"); self.type("t")  # A step typed in full: Enter is a new line again.
        self.assertEqual(self.listed(2), ["project", "project-away"])
        self.press("Return")
        self.assertEqual(self.query(), "SigninLogs\n| project\n")
        self.put("SigninLogs\n"); self.type("|"); self.assertIsNotNone(self.editor.offer)
        self.press("Return")  # A | at the end of a line, then Enter: nothing is inserted.
        self.assertEqual(self.query(), "SigninLogs\n|\n")

    def test_arrows_choose_escape_closes_and_other_keys_fall_through(self):
        self.put("SigninLogs\n| where Us"); self.focus(); self.type("e")
        self.press("Down"); self.press("Down"); self.press("Up")
        self.assertEqual(self.editor.offer.choices[self.editor.chosen].text, "UserDisplayName")
        self.assertEqual(self.text.index("insert"), "2.11")  # The caret didn't move with the arrows.
        self.type("r")  # Still typing: the row picked with the arrows stays picked.
        self.assertEqual(self.editor.offer.choices[self.editor.chosen].text, "UserDisplayName")
        self.press("Escape")
        self.assertIsNone(self.editor.offer); self.assertEqual(self.query(), "SigninLogs\n| where User")
        self.press("Up"); self.assertEqual(self.text.index("insert"), "1.10")  # Closed: Up moves the caret again.
        self.put("SigninLogs\n| where Us"); self.type("e"); self.assertIsNotNone(self.editor.offer)
        self.press("Left"); self.assertIsNone(self.editor.offer)  # The caret left the word.
        self.put("SigninLogs\n| where Us"); self.type("e"); self.type(" ")
        self.assertIsNone(self.editor.offer)  # The word is finished.
        self.put("SigninLogs\n| where Us"); self.type("e"); self.press("BackSpace"); self.press("BackSpace")
        self.assertEqual(self.listed(1), ["UserPrincipalName"])  # Back to one letter: an open list stays.
        self.press("BackSpace"); self.assertIsNone(self.editor.offer)

    def test_control_space_opens_the_list_anywhere(self):
        self.put("SigninLogs\n| where "); self.focus()
        self.text.event_generate("<Control-space>"); self.root.update()
        self.assertEqual(self.listed(2), ["UserPrincipalName", "UserDisplayName"])
        self.assertEqual(self.query(), "SigninLogs\n| where ")  # No space was typed.
        self.type("IP"); self.assertEqual(self.listed(1), ["IPAddress"])
        self.press("Tab"); self.assertEqual(self.query(), "SigninLogs\n| where IPAddress")
        # KQL is case-sensitive, so a name typed in its own case is listed first; the other case still finds it.
        self.put("SigninLogs\n| where "); self.type("ip")
        self.assertEqual(self.listed(1), ["ipv4_is_private"]); self.assertIn("IPAddress", self.listed(60))

    def test_a_function_is_inserted_with_the_caret_between_its_brackets(self):
        self.put("SigninLogs\n| where TimeGenerated > a"); self.focus(); self.type("g")
        self.assertEqual(self.listed(1), ["ago"]); self.press("Tab")
        self.assertEqual(self.query(), "SigninLogs\n| where TimeGenerated > ago()")
        self.assertEqual(self.text.get("insert", "insert+1c"), ")")
        self.text.edit_undo()  # One step back takes the whole insertion away.
        self.assertEqual(self.query(), "SigninLogs\n| where TimeGenerated > ag")
        # Brackets that are already there aren't written twice.
        self.put("T | extend x = tostr(Name)"); self.text.mark_set("insert", "1.20"); self.type("i")
        self.assertEqual(self.listed(1), ["tostring"]); self.press("Tab")
        self.assertEqual(self.query(), "T | extend x = tostring(Name)")

    def test_a_click_inserts_and_the_wheel_scrolls_only_the_list(self):
        self.put("SigninLogs\n"); self.focus(); self.type("| ")
        view = self.editor.list
        self.assertEqual(int(view.cget("height")), kql_editor.ROWS)
        x, y, width, height = view.bbox("3.1")
        view.event_generate("<Motion>", x=x + 2, y=y + height // 2); self.root.update()
        self.assertEqual(self.editor.chosen, 2)
        view.event_generate("<MouseWheel>", x=x, y=y, delta=-120); self.root.update()
        self.assertGreater(view.yview()[0], 0); view.yview_moveto(0); self.root.update()
        view.event_generate("<Button-1>", x=x + 2, y=y + height // 2); self.root.update()
        self.assertEqual(self.query(), "SigninLogs\n| project ")
        self.assertIs(self.root.focus_get(), self.text)  # The keyboard never left the query.

    def test_the_list_is_drawn_under_the_word_and_goes_when_the_box_loses_the_keyboard(self):
        other = tk.Entry(self.root); other.pack()
        self.put("SigninLogs\n| where Us"); self.focus(); self.type("e")
        popup, text = self.editor.popup, self.text
        x, y, width, height = text.bbox("2.8")
        self.assertGreaterEqual(popup.winfo_rooty(), text.winfo_rooty() + y + height)
        self.assertLess(abs(self.editor.list.winfo_rootx() + self.editor.list.bbox("1.1")[0] - (text.winfo_rootx() + x)), 3)
        self.assertLessEqual(popup.winfo_rootx() + popup.winfo_width(), self.root.winfo_rootx() + self.root.winfo_width())
        self.assertIn("Tab inserts", self.editor.hint.cget("text"))
        other.focus_force(); self.root.update()
        self.assertIsNone(self.editor.offer); self.assertFalse(popup.winfo_ismapped())

    def test_the_list_stays_when_the_window_is_only_raised_and_goes_when_it_is_resized(self):
        self.put("SigninLogs\n| where Us"); self.focus(); self.type("e")
        root = self.root
        # Windows sends <Configure> when a window is raised, made topmost or moved, its size unchanged.
        root.attributes("-topmost", True); root.lift(); root.update()
        root.event_generate("<Configure>", width=root.winfo_width(), height=root.winfo_height()); root.update()
        self.assertEqual(self.listed(1), ["UserPrincipalName"]); self.assertTrue(self.editor.popup.winfo_ismapped())
        root.geometry("960x720"); root.update()
        self.assertIsNone(self.editor.offer); self.assertFalse(self.editor.popup.winfo_ismapped())

    def test_nothing_is_offered_in_a_selection_or_a_read_only_box(self):
        self.put("SigninLogs\n| where Use"); self.focus()
        self.text.tag_add("sel", "1.0", "1.4"); self.editor.suggest(force=True)
        self.assertIsNone(self.editor.offer)
        self.text.tag_remove("sel", "1.0", "end"); self.text.configure(state="disabled"); self.editor.suggest(force=True)
        self.assertIsNone(self.editor.offer)
        self.assertEqual(self.editor.fixes(), [])


class FixTests(EditorTestCase):
    def test_every_suggested_spelling_goes_in_as_one_step(self):
        report = self.put(SLIPS)
        fixes = self.editor.fixes()
        self.assertEqual([issue.fix for issue in fixes], ["SigninLogs", "where", "and", "contains", "count", "tostring"])
        self.assertEqual(fix_label(fixes), "Fix all 6")
        self.assertEqual(fix_label(fixes[:1]), "Change to SigninLogs"); self.assertEqual(fix_label([]), "")
        self.assertEqual(self.editor.apply(fixes), 6)
        self.assertEqual(self.query(), FIXED)
        self.assertEqual(self.editor.report.issues, [])  # Checked again straight away.
        self.assertEqual(self.tagged("kql-warning"), [])
        self.text.edit_undo(); self.assertEqual(self.query(), SLIPS)
        self.assertIsNot(report, self.editor.report)

    def test_a_fix_is_only_put_in_where_the_query_still_says_what_was_checked(self):
        self.put("SignLogs\n| wher x == 1")
        self.text.delete("2.2", "2.6"); self.text.insert("2.2", "take")  # Changed since the check.
        self.assertEqual([issue.fix for issue in self.editor.fixes()], ["SigninLogs"])
        self.assertEqual(self.editor.apply(self.editor.report.fixes), 1)
        self.assertEqual(self.query(), "SigninLogs\n| take x == 1")
        self.assertEqual(self.editor.apply([]), 0)

    def test_control_period_fixes_the_slip_at_the_caret(self):
        self.put("SignLogs\n| wher a == 1 && b == 2"); self.focus()
        self.text.mark_set("insert", "2.4")
        self.text.event_generate("<Control-period>"); self.root.update()
        self.assertEqual(self.query(), "SignLogs\n| where a == 1 && b == 2")
        self.text.mark_set("insert", "2.12")  # Not on a slip: the first one on the line.
        self.text.event_generate("<Control-period>"); self.root.update()
        self.assertEqual(self.query(), "SignLogs\n| where a == 1 and b == 2")
        self.text.event_generate("<Control-period>"); self.root.update()  # Nothing left on this line.
        self.assertEqual(self.query(), "SignLogs\n| where a == 1 and b == 2")

    def test_right_click_on_a_slip_offers_its_spelling_above_the_edit_menu(self):
        self.put("SignLogs\n| take 1"); self.focus()
        shown = []
        with patch.object(kit, "_show_menu", side_effect=lambda menu, event: shown.append(menu)):
            x, y, width, height = self.text.bbox("1.3")
            self.text.event_generate("<Button-3>", x=x + 1, y=y + height // 2); self.root.update()
            menu = shown.pop()
            labels = [menu.entrycget(index, "label") for index in range(menu.index("end") + 1) if menu.type(index) == "command"]
            self.assertEqual(labels, ["Change to SigninLogs", "Cut", "Copy", "Paste", "Select all"])
            self.assertEqual(str(menu.entrycget(2, "state")), "disabled")  # Cut: nothing is selected.
            menu.invoke(0)
            self.assertEqual(self.query(), "SigninLogs\n| take 1")
            # Anywhere else, the kit's own menu opens (install_edit_menu binds the Text class).
            edit = kit.install_edit_menu(self.root)
            x, y, width, height = self.text.bbox("2.3")
            self.text.event_generate("<Button-3>", x=x + 1, y=y + height // 2); self.root.update()
            self.assertIs(shown.pop(), edit)


class BuilderTests(unittest.TestCase):
    """The editor on the Rule builder's Query step: the links under the box and the remembered theme."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        p = patch.object(ui, "DATA", Path(self.temp.name)); p.start(); self.addCleanup(p.stop)
        self.root = None
        self.addCleanup(self.cleanup)
        self.start()

    def start(self):
        self.cleanup()
        self.root = tk.Tk(); self.root.withdraw()
        self.app = ui.App(self.root); self.root.update()
        self.page = self.app.rule_builder
        self.app.show_page("builder"); self.page.steps.select(1); self.root.update()

    def cleanup(self):
        if self.root is None: return
        self.app.set_busy(False)
        for timer in self.root.tk.call("after", "info"): self.root.after_cancel(timer)
        self.root.destroy(); self.root = None

    def put(self, query):
        text = self.page.texts["query"]
        text.delete("1.0", "end"); text.insert("1.0", query); self.root.update()
        return text

    def test_the_query_is_coloured_as_it_is_typed_and_checked_after_a_pause(self):
        text = self.put("SignLogs\n| where x == 1")
        self.assertTrue(text.tag_ranges("syntax-step"))        # At once.
        self.assertFalse(text.tag_ranges("kql-warning"))       # After the pause.
        self.assertIsNotNone(self.page.kql_job)
        self.page.check_kql()
        self.assertEqual([str(index) for index in text.tag_ranges("kql-warning")], ["1.0", "1.8"])
        self.assertIsNone(self.page.kql_job)

    def test_the_fix_link_follows_the_check_and_puts_the_spellings_in(self):
        page = self.page
        text = self.put(SLIPS); page.check_kql()
        self.assertEqual(page.kql_fix.cget("text"), "Fix all 6"); self.assertTrue(page.kql_fix.winfo_manager())
        page.fix_kql(); self.root.update()
        self.assertEqual(text.get("1.0", "end-1c"), FIXED)
        self.assertFalse(page.kql_fix.winfo_manager())
        self.assertEqual(page.kql_line.cget("text"), "No problems found. Reads SigninLogs.")
        self.put("SigninLogs | wher x"); page.check_kql()
        self.assertEqual(page.kql_fix.cget("text"), "Change to where")
        self.put('SigninLogs | where a == "open'); page.check_kql()  # An error has no spelling to suggest.
        self.assertFalse(page.kql_fix.winfo_manager())
        # Ctrl+. in the box updates the line under it too.
        self.put("SigninLogs | wher x"); page.check_kql()
        text.mark_set("insert", "1.15"); page.editor.fix_here()
        self.assertEqual(page.kql_line.cget("text"), "No problems found. Reads SigninLogs.")
        self.assertFalse(page.kql_fix.winfo_manager())

    def test_the_theme_is_chosen_from_the_link_and_remembered(self):
        page = self.page
        self.assertEqual(page.kql_theme.cget("text"), "Theme: " + KQL_THEME)
        with patch.object(tk.Menu, "tk_popup"):
            page.show_kql_themes()
        menu = page.kql_theme_menu
        self.assertEqual([menu.entrycget(index, "label") for index in range(menu.index("end") + 1)], list(KQL_THEMES))
        menu.invoke(1)
        self.assertEqual(page.editor.theme, "Midnight")
        self.assertEqual(page.kql_theme.cget("text"), "Theme: Midnight")
        self.assertEqual(str(page.texts["query"].cget("bg")).lower(), KQL_THEMES["Midnight"]["background"].lower())
        self.app.save_state()
        self.start()  # The next start opens with it.
        self.assertEqual(self.page.editor.theme, "Midnight")
        self.assertEqual(self.page.kql_theme.cget("text"), "Theme: Midnight")
        self.app.ui_state["kql_theme"] = 7; self.app.save_state()  # A damaged value falls back to the default.
        self.start()
        self.assertEqual(self.page.editor.theme, KQL_THEME)

    def test_the_list_closes_when_the_step_or_the_page_changes(self):
        page, text = self.page, self.put("SigninLogs\n| where Us")
        self.root.deiconify(); self.root.update(); text.focus_force(); self.root.update()
        text.mark_set("insert", "end-1c")
        for leave in (lambda: page.steps.select(0), lambda: self.app.show_page("connect")):
            self.app.show_page("builder"); page.steps.select(1); self.root.update()
            page.editor.suggest(force=True); self.root.update()
            self.assertTrue(page.editor.popup.winfo_ismapped())
            leave(); self.root.update()
            self.assertIsNone(page.editor.offer); self.assertFalse(page.editor.popup.winfo_ismapped())


if __name__ == "__main__":
    unittest.main()
