"""Read-only repository catalog. Never imports or executes repository scripts."""
import base64
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import quote

MAX_FILE = 2 * 1024 * 1024
MAX_TOTAL = 32 * 1024 * 1024
MAX_FILES = 2000


class CatalogError(ValueError):
    pass


def category(path):
    p = PurePosixPath(path)
    if p.is_absolute() or any(x in ("..", ".") for x in path.split("/")) or "\\" in path:
        return None
    if p.suffix.lower() not in (".yml", ".yaml"):
        return None
    if len(p.parts) >= 3 and p.parts[0] == "clients":
        return "clients"
    if len(p.parts) >= 3 and p.parts[:2] == ("rules", "sentinel"):
        return "rules"
    return None


def parse_yaml(raw):
    try:
        import yaml
    except ImportError as exc:
        raise CatalogError("Repository browsing needs PyYAML. Install requirements.txt using this app's Python.") from exc
    if len(raw) > MAX_FILE:
        raise CatalogError("File exceeds the 2 MB catalog limit.")
    class UniqueLoader(yaml.SafeLoader):
        pass
    def mapping(loader, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in result:
                raise CatalogError("YAML keys must be unique strings.")
            result[key] = loader.construct_object(value_node, deep=deep)
        return result
    UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
    try:
        # Reject aliases to prevent cycles and unexpected expansion in raw detail views.
        if any(isinstance(event, yaml.AliasEvent) for event in yaml.parse(raw)):
            raise CatalogError("YAML aliases are not supported by this catalog reader.")
        value = yaml.load(raw, Loader=UniqueLoader)
    except (yaml.YAMLError, UnicodeError, RecursionError) as exc:
        raise CatalogError("Invalid or unsupported YAML.") from exc
    if not isinstance(value, dict):
        raise CatalogError("Expected a YAML object.")
    return value


def state(value):
    return "Enabled" if value is True else "Disabled" if value is False else "Not specified"


def catalog(files, source, revision, identity="Local folder", problems=()):
    rows = {"clients": [], "rules": []}
    issues = list(problems)
    if len(files) > MAX_FILES or sum(len(v) for v in files.values()) > MAX_TOTAL:
        raise CatalogError("Catalog exceeds its file count or total size limit.")
    # Missing dependencies must stop the load, not masquerade as invalid client files.
    parse_yaml(b"catalog: true")
    seen = set()
    for path, raw in sorted(files.items()):
        kind = category(path)
        if not kind:
            continue
        try:
            data = parse_yaml(raw)
            if kind == "clients":
                if data.get("version") != 1 or type(data.get("version")) is not int:
                    raise CatalogError("Unsupported workspace schema version; expected 1.")
                target, client = data.get("target"), data.get("client")
                for name in (target, client):
                    if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) or len(name) > 64:
                        raise CatalogError("Invalid client or target identifier.")
                if target in seen:
                    raise CatalogError("Duplicate target identifier: " + target)
                if type(data.get("enabled")) is not bool or not isinstance(data.get("azure"), dict):
                    raise CatalogError("Workspace needs an enabled boolean and Azure settings.")
                selections = data.get("rules")
                if not isinstance(selections, list):
                    raise CatalogError("Workspace rules must be a list.")
                paths = set()
                for selection in selections:
                    if not isinstance(selection, dict) or not isinstance(selection.get("path"), str) or category(selection["path"]) != "rules":
                        raise CatalogError("Invalid rule assignment path.")
                    if not isinstance(selection.get("overrides", {}), dict):
                        raise CatalogError("Rule overrides must be an object.")
                    if selection["path"] in paths:
                        raise CatalogError("Duplicate rule assignment: " + selection["path"])
                    paths.add(selection["path"])
                seen.add(target)
                rows[kind].append(dict(path=path, name=client, target=target,
                    workspace=str(data["azure"].get("workspace_name", "Not specified")),
                    state=state(data["enabled"]), assignments=selections, raw=data))
            else:
                name = data.get("name") or data.get("displayName")
                if not isinstance(name, str) or not name.strip() or not isinstance(data.get("id"), str):
                    raise CatalogError("Rule needs a name and string id.")
                rows[kind].append(dict(path=path, name=name, id=data["id"],
                    severity=str(data.get("severity", "Not specified")),
                    status=str(data.get("status", "Not specified")),
                    state=state(data.get("enabled")), raw=data))
        except CatalogError as exc:
            issues.append(path + ": " + str(exc))
    available = {r["path"] for r in rows["rules"]}
    for client in rows["clients"]:
        for assignment in client["assignments"]:
            if assignment["path"] not in available:
                issues.append(client["path"] + ": assigned rule unavailable: " + assignment["path"])
    for kind in rows:
        rows[kind].sort(key=lambda r: (r["name"].casefold(), r["path"]))
    return dict(**rows, issues=issues, source=source, revision=revision, identity=identity,
                loaded_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))


def load_local(folder):
    root = Path(folder).expanduser().resolve(strict=True)
    files, issues, total = {}, [], 0
    for sub in ("clients", "rules/sentinel"):
        base = root / sub
        if not base.is_dir():
            raise CatalogError("Choose the detection repository root containing clients and rules/sentinel.")
        if any(p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction()) for p in [base, *base.parents] if p != root and root in p.parents):
            raise CatalogError("Catalog directories cannot be symbolic links or junctions.")
        def walk_error(error):
            raise CatalogError("Cannot read a catalog directory: " + str(error))
        for directory, dirs, names in os.walk(base, followlinks=False, onerror=walk_error):
            for name in list(dirs):
                child = Path(directory) / name
                if child.is_symlink() or (hasattr(child, "is_junction") and child.is_junction()):
                    dirs.remove(name); issues.append(child.relative_to(root).as_posix() + ": linked directory skipped.")
            for name in sorted(names):
                path = Path(directory) / name
                rel = path.relative_to(root).as_posix()
                if not category(rel):
                    continue
                if path.is_symlink() or not path.resolve().is_relative_to(root):
                    issues.append(rel + ": linked file skipped."); continue
                with path.open("rb") as stream:
                    raw = stream.read(MAX_FILE + 1)
                if len(raw) > MAX_FILE:
                    issues.append(rel + ": file exceeds 2 MB limit."); continue
                files[rel] = raw; total += len(raw)
                if len(files) > MAX_FILES or total > MAX_TOTAL:
                    raise CatalogError("Catalog exceeds its file count or total size limit.")
    return catalog(files, str(root), "Local working files — may include unpublished changes", problems=issues)


class GitHubReader:
    """Temporary CLI authentication adapter. All network requests are explicit GETs."""
    def __init__(self, request=None):
        self.request = request or self.get

    @staticmethod
    def get(endpoint):
        executable = shutil.which("gh")
        if not executable:
            raise CatalogError("GitHub CLI is not installed. Use a local folder or install GitHub CLI and sign in.")
        try:
            result = subprocess.run([executable, "api", "--hostname", "github.com", "--method", "GET", endpoint],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired as exc:
            raise CatalogError("GitHub timed out. Refresh to try this read again.") from exc
        if result.returncode:
            raise CatalogError("GitHub read failed. Check the signed-in account, repository name and organization access. "
                               "For a private repository, Not Found can mean no access. Run gh auth login if needed.")
        return json.loads(result.stdout)

    def load(self, repository):
        parse_yaml(b"catalog: true")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+", repository):
            raise CatalogError("Enter a repository as owner/name.")
        prefix = "repos/" + repository
        identity = self.request("user")["login"]
        try:
            meta = self.request(prefix)
        except CatalogError as exc:
            raise CatalogError("Signed in as " + identity + ". " + str(exc)) from exc
        branch = meta["default_branch"]
        commit = self.request(prefix + "/commits/" + quote(branch, safe=""))
        revision = commit["sha"]
        tree = self.request(prefix + "/git/trees/" + commit["commit"]["tree"]["sha"] + "?recursive=1")
        if tree.get("truncated"):
            raise CatalogError("GitHub returned an incomplete file tree. Use a local clone for this catalog.")
        paths = {entry["path"] for entry in tree["tree"]}
        if not {"clients", "rules/sentinel"}.issubset(paths):
            raise CatalogError("Repository must contain clients and rules/sentinel directories.")
        files, issues, total = {}, [], 0
        entries = [e for e in tree["tree"] if category(e["path"]) and e["type"] == "blob"]
        if len(entries) > MAX_FILES:
            raise CatalogError("Catalog exceeds the file count limit.")
        for entry in entries:
            path = entry["path"]
            if entry.get("mode") not in ("100644", "100755"):
                issues.append(path + ": linked file skipped."); continue
            if entry.get("size", 0) > MAX_FILE:
                issues.append(path + ": file exceeds 2 MB limit."); continue
            blob = self.request(prefix + "/git/blobs/" + entry["sha"])
            if blob.get("encoding") != "base64":
                raise CatalogError("GitHub returned an unsupported blob encoding.")
            raw = base64.b64decode(blob["content"])
            total += len(raw)
            if len(raw) > MAX_FILE or total > MAX_TOTAL:
                raise CatalogError("Catalog exceeds its file size limit.")
            files[path] = raw
        result = catalog(files, repository + " / " + branch, revision, identity, issues)
        result.update(repository=repository, repository_id=meta.get("id"),
                      private=meta.get("private"), can_push=meta.get("permissions", {}).get("push"),
                      base_branch=branch, tree_sha=commit["commit"]["tree"]["sha"],
                      files=files, entries={entry["path"]: entry for entry in tree["tree"]})
        return result
