"""Rename tracked primary.yml files and their references. Python 3.10+, Git."""
import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

REFERENCE = re.compile(r"(?<![A-Za-z0-9_.-])primary\.yml(?![A-Za-z0-9_.-])")


def git(root, *args):
    executable = shutil.which("git")
    if not executable:
        candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/git.exe"
        if candidate.is_file():
            executable = str(candidate)
    if not executable:
        raise RuntimeError("Git is required. Install Git for Windows, then reopen this script.")
    result = subprocess.run([executable, "-C", str(root), *args], capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace").strip())
    return result.stdout


def plan(folder):
    root = Path(git(folder, "rev-parse", "--show-toplevel").decode("utf-8").strip()).resolve()
    entries = git(root, "ls-files", "--stage", "-z").decode("utf-8").split("\0")
    changes = []
    for entry in filter(None, entries):
        metadata, name = entry.split("\t", 1)
        mode, _, stage = metadata.split()
        if stage != "0":
            raise RuntimeError("Resolve merge conflicts before running this script.")
        if mode not in ("100644", "100755"):
            continue
        source = root / name
        # The utility may itself be stored in the repository. Never rewrite it.
        if source.resolve() == Path(__file__).resolve():
            continue
        if source.is_symlink() or not source.is_file():
            continue
        if not source.resolve().is_relative_to(root):
            raise RuntimeError(f"File resolves outside the repository: {name}")
        original = source.read_bytes()
        updated = original
        replacements = 0
        if b"\0" not in original:
            try:
                content = original.decode("utf-8")
            except UnicodeDecodeError:
                if b"primary.yml" in original or source.name == "primary.yml":
                    raise RuntimeError(f"Review non-UTF-8 file manually: {name}")
            else:
                content, replacements = REFERENCE.subn("workspace.yml", content)
                updated = content.encode("utf-8")
        destination = source.with_name("workspace.yml") if source.name == "primary.yml" else source
        if destination != source and destination.exists():
            raise RuntimeError(f"Will not overwrite existing file: {destination.relative_to(root)}")
        if destination != source or original != updated:
            changes.append((source, destination, original, updated, replacements))
    return root, changes


def report(root, changes):
    lines = ["Rename Workspace v2", f"Repository: {root}", ""]
    for source, destination, _, _, count in changes:
        name = source.relative_to(root).as_posix()
        if source != destination:
            lines.append(f"RENAME {name} -> {destination.relative_to(root).as_posix()}")
        if count:
            lines.append(f"UPDATE {name}: {count} filename reference(s)")
    lines.extend(["", f"{len(changes)} tracked file(s) affected.",
                  "Target IDs, environment names, app names, and OIDC subjects are unchanged.",
                  "No commit, push, Azure change, or deployment is performed."])
    if not changes:
        candidates = [name for name in git(root, "ls-files", "-z").decode("utf-8").split("\0")
                      if name.startswith("clients/") and name.endswith((".yml", ".yaml"))]
        lines.extend(["", "No rename was made. Git tracks no primary.yml file or filename references here."])
        if candidates:
            lines.append("Tracked client YAML files:")
            lines.extend(f"  {name}" for name in candidates[:30])
            if len(candidates) > 30:
                lines.append(f"  ...and {len(candidates) - 30} more")
        else:
            lines.append("No client YAML files are tracked in this checkout.")
        lines.append("Check the selected repository folder and branch. If workspace.yml already exists, the filename change is complete locally.")
    return "\n".join(lines)


def apply(root, changes):
    if git(root, "status", "--porcelain", "--untracked-files=no").strip():
        raise RuntimeError("Commit or stash tracked-file changes first, then preview again.")
    for source, destination, original, _, _ in changes:
        if source.read_bytes() != original or (source != destination and destination.exists()):
            raise RuntimeError("Files changed after preview. Run the preview again.")
    completed = []
    try:
        for source, destination, original, updated, _ in changes:
            if source != destination:
                # Use exclusive creation so an existing file can never be overwritten.
                with destination.open("xb") as stream:
                    completed.append((source, destination, original))
                    stream.write(updated)
                shutil.copymode(source, destination)
                source.unlink()
            else:
                completed.append((source, destination, original))
                source.write_bytes(updated)
    except Exception:
        for source, destination, original in reversed(completed):
            source.write_bytes(original)
            if source != destination and destination.exists():
                destination.unlink()
        raise


def gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox
    window = tk.Tk()
    window.withdraw()
    folder = filedialog.askdirectory(title="Select your local detection-as-code repository")
    if not folder:
        window.destroy()
        return
    try:
        root, changes = plan(folder)
    except Exception as error:
        messagebox.showerror("Cannot prepare rename", str(error))
        window.destroy()
        return
    window.deiconify()
    window.title("Rename Workspace v2 — Preview")
    window.geometry("900x600")
    text = tk.Text(window, wrap="word", padx=15, pady=15)
    text.pack(fill="both", expand=True)
    text.insert("1.0", report(root, changes))
    text.configure(state="disabled")

    def execute():
        try:
            apply(root, changes)
        except Exception as error:
            messagebox.showerror("Rename not completed", str(error))
            return
        button.configure(state="disabled")
        messagebox.showinfo("Rename complete", "Files updated locally. Review the Git diff and run the repository's offline validation before committing and pushing.")

    button = tk.Button(window, text="Apply these changes", command=execute,
                       state="normal" if changes else "disabled", padx=20, pady=10)
    button.pack(pady=10)
    window.mainloop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", nargs="?", help="Local repository folder; omit to use the graphical picker")
    parser.add_argument("--apply", action="store_true", help="Apply changes locally; default is preview only")
    args = parser.parse_args()
    if not args.repository:
        if args.apply:
            parser.error("--apply requires a repository folder")
        gui()
        return
    root, changes = plan(args.repository)
    print(report(root, changes))
    if args.apply:
        apply(root, changes)
        print("\nApplied locally. Review the Git diff and run offline validation before committing.")
    else:
        print("\nPreview only. Add --apply to perform these changes.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
