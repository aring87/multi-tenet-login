"""Reviewed single-rule pull requests. No writes to a default branch or Azure."""
import difflib
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
from urllib.parse import quote, urlencode
import uuid

from .repository_catalog import GitHubReader, category, parse_yaml
from .rule_drafts import atomic_write, read_bounded
from . import rule_schema


class ReviewError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ReviewError(message)


def api(method, endpoint, body=None):
    executable = shutil.which("gh")
    require(executable, "Install GitHub CLI and sign in using gh auth login.")
    args = [executable, "api", "--hostname", "github.com", "--method", method,
            "-H", "Accept: application/vnd.github+json", endpoint]
    if body is not None:
        args += ["--input", "-"]
    try:
        result = subprocess.run(args, input=json.dumps(body) if body is not None else None,
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as error:
        raise ReviewError("GitHub timed out. A write may have completed. Use the same saved review request to recover it.") from error
    if result.returncode:
        # Avoid echoing private query content or credentials from CLI output.
        raise ReviewError("GitHub request failed. Check GitHub sign-in, organization access and Contents/Pull requests permissions. "
                          "Keep this review request to recover any completed writes.")
    try:
        return json.loads(result.stdout)
    except ValueError as error:
        raise ReviewError("GitHub returned an unreadable response. Recover using this same request.") from error


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def check_path(path):
    require(isinstance(path, str) and len(path) <= 240 and
            re.fullmatch(r"[A-Za-z0-9_./-]+", path) and "//" not in path and
            category(path) == "rules" and path.endswith((".yml", ".yaml")), "Use a YAML file path under rules/sentinel, without spaces or traversal.")


def diff(plan):
    return "".join(difflib.unified_diff(plan["before"].splitlines(keepends=True),
        plan["content"].splitlines(keepends=True), fromfile="before/" + plan["path"], tofile="after/" + plan["path"]))


def save_request(folder, record):
    request_id = record["plan"]["request_id"]
    require(re.fullmatch(r"[0-9a-f]{32}", request_id), "Invalid review request ID.")
    folder = Path(folder); folder.mkdir(parents=True, exist_ok=True)
    path = folder / (request_id + ".review.json")
    atomic_write(path, json.dumps(record, indent=2) + "\n")
    return path


def check_record(record):
    require(isinstance(record, dict) and record.get("version") == 1, "Unsupported review request.")
    plan = record.get("plan")
    require(isinstance(plan, dict) and record.get("fingerprint") == fingerprint(plan),
            "Review request changed. Prepare a fresh review.")
    require(re.fullmatch(r"[0-9a-f]{32}", plan.get("request_id", "")), "Invalid request ID.")
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+", plan.get("repository", "")), "Invalid repository.")
    check_path(plan["path"])
    require(plan.get("mode") in ("100644", "100755"), "Review file must be an ordinary file.")
    for key in ("base_sha", "tree_sha"):
        require(re.fullmatch(r"[0-9a-f]{40}", plan.get(key, "")), "Invalid reviewed Git revision.")
    if record.get("commit_sha") is not None:
        require(re.fullmatch(r"[0-9a-f]{40}", record["commit_sha"]), "Invalid saved commit revision.")
    require(isinstance(plan["allow_missing_mitre"], bool), "Invalid migration option.")
    rule_schema.validate(parse_yaml(plan["content"].encode()), plan["allow_missing_mitre"])
    return plan


def load_request(path):
    record = json.loads(read_bounded(path)); check_record(record)
    return record


class ReviewService:
    def __init__(self, request=None):
        self.request = request or api

    def prepare(self, repository, path, content, allow_missing_mitre=False, source=None):
        check_path(path)
        rule = parse_yaml(content.encode("utf-8"))
        warnings = rule_schema.validate(rule, allow_missing_mitre)
        rule_schema.to_arm(rule)
        snapshot = GitHubReader(lambda endpoint: self.request("GET", endpoint)).load(repository)
        require(snapshot["private"] is True, "Select the private detection repository, not the public application repository.")
        require(snapshot["can_push"] is True, "This GitHub account needs write access to the detection repository.")
        require(not snapshot["issues"], "The repository catalog is incomplete or invalid. Resolve its catalog issues before submitting a rule.")
        entries = snapshot["entries"]
        for prefix in ["/".join(path.split("/")[:i]) for i in range(1, len(path.split("/")))]:
            entry = entries.get(prefix)
            require(entry is None or (entry["type"] == "tree" and entry.get("mode") == "040000"),
                    "Rule path contains a linked or non-directory parent.")
        entry = entries.get(path)
        before = ""
        if entry is not None:
            require(entry["type"] == "blob" and entry.get("mode") in ("100644", "100755"), "Destination is not an ordinary rule file.")
            require(source and source.get("repository", "").casefold() == repository.casefold()
                    and source.get("path") == path and source.get("revision") == snapshot["revision"],
                    "This file already exists or its source is stale. Refresh the GitHub catalog and edit that rule as a draft.")
            before = snapshot["files"][path].decode("utf-8-sig")
            original = parse_yaml(before.encode())
            require(original["id"].lower() == rule["id"].lower(), "Editing an existing rule must preserve its rule ID.")
        else:
            require(not source, "The original repository rule is missing or the destination changed. Start a new draft for a new rule.")
        for other in snapshot["rules"]:
            require(other["path"] == path or other["id"].lower() != rule["id"].lower(),
                    "This rule ID already exists at " + other["path"] + ". Start a new draft for a distinct rule.")
        require(before != content, "There are no file changes to submit.")
        impact = []
        for client in snapshot["clients"]:
            for assignment in client["assignments"]:
                if assignment["path"] == path:
                    overrides = assignment.get("overrides", {})
                    effective = rule_schema.apply_overrides(rule, overrides)
                    # Use each target's own migration setting; the editor checkbox cannot authorize it.
                    warnings += [client["target"] + ": " + warning for warning in
                                 rule_schema.validate(effective, client["raw"].get("allow_missing_mitre") is True)]
                    impact.append({"target": client["target"], "target_enabled": client["raw"]["enabled"],
                                   "rule_enabled": effective["enabled"], "overrides": overrides})
        plan = dict(request_id=uuid.uuid4().hex, repository=repository, repository_id=snapshot["repository_id"],
                    identity=snapshot["identity"], base_branch=snapshot["base_branch"], base_sha=snapshot["revision"],
                    tree_sha=snapshot["tree_sha"], path=path, content=content, before=before,
                    mode=entry["mode"] if entry else "100644", name=rule["name"], impact=impact,
                    allow_missing_mitre=allow_missing_mitre, warnings=warnings)
        require(plan["repository_id"] is not None, "GitHub did not identify the repository.")
        return dict(version=1, plan=plan, fingerprint=fingerprint(plan), commit_sha=None, pull_request=None)

    def verify_saved_commit(self, prefix, plan, commit_sha):
        # A saved request is input, not authority to publish an arbitrary commit.
        base = self.request("GET", prefix + "/git/commits/" + plan["base_sha"])
        commit = self.request("GET", prefix + "/git/commits/" + commit_sha)
        require(base["tree"]["sha"] == plan["tree_sha"] and
                [parent["sha"] for parent in commit["parents"]] == [plan["base_sha"]],
                "Saved commit has a different base. It will not be published.")
        # Recreating a Git tree is deterministic and does not change any branch.
        expected = self.request("POST", prefix + "/git/trees", {
            "base_tree": plan["tree_sha"], "tree": [{"path": plan["path"], "mode": plan["mode"],
            "type": "blob", "content": plan["content"]}]})
        require(commit["tree"]["sha"] == expected["sha"],
                "Saved commit contains changes outside the reviewed diff. It will not be published.")

    def submit(self, record, folder):
        plan = check_record(record)
        prefix = "repos/" + plan["repository"]
        metadata = self.request("GET", prefix)
        identity = self.request("GET", "user")["login"]
        require(metadata.get("private") is True and metadata.get("id") == plan["repository_id"],
                "Repository identity or visibility changed. Prepare a new review.")
        require(metadata.get("permissions", {}).get("push") is True, "GitHub write access is no longer available.")
        require(identity == plan["identity"], "GitHub account changed. Prepare a new review with the current account.")
        require(metadata["default_branch"] == plan["base_branch"], "Default branch changed. Prepare a new review.")
        branch = "codex/rule-" + plan["request_id"]
        refs = self.request("GET", prefix + "/git/matching-refs/heads/" + quote(branch, safe=""))
        matches = [ref for ref in refs if ref["ref"] == "refs/heads/" + branch]
        require(len(matches) <= 1, "Ambiguous review branch.")
        if matches:
            require(record.get("commit_sha") and matches[0]["object"]["sha"] == record["commit_sha"],
                    "Review branch was changed outside this request. Open it in GitHub; it will not be overwritten.")
            self.verify_saved_commit(prefix, plan, record["commit_sha"])
        else:
            require(not record.get("pull_request"), "The previous review branch was removed. Open the recorded pull request instead.")
            latest = self.request("GET", prefix + "/commits/" + quote(plan["base_branch"], safe=""))
            require(latest["sha"] == plan["base_sha"] and latest["commit"]["tree"]["sha"] == plan["tree_sha"], "The repository changed after preview. Prepare a fresh review before submitting.")
            save_request(folder, record)  # Must succeed before any remote write.
            if not record.get("commit_sha"):
                tree = self.request("POST", prefix + "/git/trees", {
                    "base_tree": plan["tree_sha"], "tree": [{"path": plan["path"], "mode": plan["mode"],
                    "type": "blob", "content": plan["content"]}]})
                commit = self.request("POST", prefix + "/git/commits", {
                    "message": "Review Sentinel rule: " + plan["name"], "tree": tree["sha"], "parents": [plan["base_sha"]]})
                record["commit_sha"] = commit["sha"]
                save_request(folder, record)
            else:
                self.verify_saved_commit(prefix, plan, record["commit_sha"])
            self.request("POST", prefix + "/git/refs", {"ref": "refs/heads/" + branch, "sha": record["commit_sha"]})
        query = urlencode({"state": "all", "head": plan["repository"].split("/")[0] + ":" + branch,
                           "base": plan["base_branch"], "per_page": 100})
        pulls = self.request("GET", prefix + "/pulls?" + query)
        require(len(pulls) <= 1, "Multiple pull requests exist for this request. Review them in GitHub.")
        if pulls:
            pull = pulls[0]
            require(pull["head"]["sha"] == record["commit_sha"] and pull["head"]["ref"] == branch
                    and pull["base"]["ref"] == plan["base_branch"]
                    and pull["head"]["repo"]["id"] == plan["repository_id"], "The existing pull request differs from this review.")
        else:
            body = ("Proposes a change to `" + plan["path"] + "` from the desktop rule builder.\n\n"
                    "Validated with the application's reviewed rule schema. Live KQL, repository checks, "
                    "and deployment approval remain required.\n\n"
                    "Existing target references: " + str(len(plan["impact"])) + ". No client manifests are changed.\n\n"
                    + ("Warnings: " + "; ".join(plan["warnings"]) + "\n\n" if plan["warnings"] else "")
                    + "Review request: `" + plan["request_id"] + "`\nBase commit: `" + plan["base_sha"] + "`")
            pull = self.request("POST", prefix + "/pulls", {"title": "Review Sentinel rule: " + plan["name"][:180],
                "head": branch, "base": plan["base_branch"], "body": body, "draft": True})
        url = pull["html_url"]
        require(re.fullmatch(r"https://github\.com/" + re.escape(plan["repository"]) + r"/pull/\d+", url, re.I), "Unexpected pull request URL.")
        record["pull_request"] = url
        save_request(folder, record)
        return url
