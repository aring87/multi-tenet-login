"""Explicit client selections dispatched to the repository's preview workflow."""
import base64
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import subprocess
from urllib.parse import quote
import uuid

from repository_catalog import GitHubReader, parse_yaml, MAX_FILE, state
from repository_reviews import fingerprint, require, ReviewError
from rule_drafts import atomic_write

WORKFLOW = "sentinel-multi-workspace.yml"
WORKFLOW_PATH = ".github/workflows/" + WORKFLOW


def api(method, endpoint, body=None):
    executable = shutil.which("gh")
    require(executable, "Install GitHub CLI and sign in with gh auth login.")
    args = [executable, "api", "--hostname", "github.com", "--method", method,
            "-H", "Accept: application/vnd.github+json", "-H", "X-GitHub-Api-Version: 2026-03-10", endpoint]
    if body is not None: args += ["--input", "-"]
    try:
        result = subprocess.run(args, input=json.dumps(body) if body is not None else None,
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as error:
        raise ReviewError("GitHub timed out. A submitted workflow may still have started; check Actions before starting another.") from error
    require(result.returncode == 0, "GitHub request failed. Check sign-in, organization access and Actions permissions. "
            "If submission was attempted, check Actions before starting another run.")
    if not result.stdout.strip(): return None
    try: return json.loads(result.stdout)
    except ValueError as error: raise ReviewError("GitHub returned an unreadable response. Check Actions for any attempted submission.") from error


def eligible_clients(snapshot, rule_path):
    return [c for c in snapshot["clients"] if c["raw"]["enabled"] is True and
            any(a["path"] == rule_path for a in c["assignments"])]


def input_batches(workflow, targets, rule_path, mode="preview"):
    require(mode in ("preview", "deploy"), "Unsupported workflow mode.")
    event = workflow.get("on", {}).get("workflow_dispatch")
    require(isinstance(event, dict), "The workflow must support workflow_dispatch.")
    inputs = event.get("inputs", {})
    require(isinstance(inputs, dict) and all(isinstance(v, dict) for v in inputs.values()), "Invalid workflow inputs.")
    mode_input, rule = inputs.get("mode", {}), inputs.get("rule_path", {})
    require(mode_input.get("type") == "choice" and mode in mode_input.get("options", []), f"The workflow needs a {mode} mode choice.")
    require(rule.get("type") == "string", "The workflow needs a rule_path string input.")
    slots = sorted([k for k in inputs if k == "target" or re.fullmatch(r"target_[2-9][0-9]*|target_1[0-9]+", k)],
                   key=lambda k: 1 if k == "target" else int(k.split("_")[1]))
    if "targets" in inputs:
        require(not slots and inputs["targets"].get("type") == "string", "Unsupported target input combination.")
        allowed = {"targets", "mode", "rule_path"}
        batches = [{"mode": mode, "rule_path": rule_path, "targets": ",".join(targets)}]
    else:
        require(slots and slots[0] == "target" and len(slots) <= 25, "No supported client selection inputs found.")
        require(all(inputs[k].get("type") == "choice" for k in slots), "Client slots must be dropdown choices.")
        allowed = {*slots, "mode", "rule_path"}
        batches = []
        for offset in range(0, len(targets), len(slots)):
            values = {"mode": mode, "rule_path": rule_path}
            chunk = targets[offset:offset + len(slots)]
            for i, key in enumerate(slots):
                value = chunk[i] if i < len(chunk) else "Select a client"
                require(value in inputs[key].get("options", []), "Workflow dropdown is missing " + value + ". Sync its target choices, then refresh the catalog.")
                values[key] = value
            batches.append(values)
    # Unknown optional defaults may affect the target selection or mode: fail visibly.
    require(set(inputs) == allowed, "Workflow inputs changed. Update this app's workflow adapter before running workflows.")
    return batches


class PreviewService:
    receipt_suffix = ".preview.json"

    def __init__(self, request=None):
        self.request = request or api

    def read_workflow(self, snapshot, repository):
        entry = snapshot["entries"].get(WORKFLOW_PATH, {})
        require(entry.get("type") == "blob" and entry.get("mode") in ("100644", "100755") and entry.get("size", MAX_FILE + 1) <= MAX_FILE,
                "The multi-workspace workflow is missing or is not an ordinary supported file.")
        blob = self.request("GET", "repos/" + repository + "/git/blobs/" + entry["sha"])
        require(blob.get("encoding") == "base64", "Unsupported workflow encoding.")
        return parse_yaml(base64.b64decode(blob["content"]), yaml12=True)

    def prepare(self, repository, revision, rule_path, targets, *, mode="preview"):
        snapshot = GitHubReader(lambda endpoint: self.request("GET", endpoint)).load(repository)
        require(snapshot["private"] is True and snapshot["can_push"] is True, "Use a private detection repository where your GitHub account has write access.")
        require(snapshot["base_branch"] == "main", "This workflow adapter requires the default branch to be main.")
        require(snapshot["revision"] == revision, "The repository changed. Refresh the catalog and review the selection again.")
        require(not snapshot["issues"], "Resolve the catalog issues before preparing previews.")
        require(any(r["path"] == rule_path for r in snapshot["rules"]), "Select a rule in the current GitHub catalog.")
        require(isinstance(targets, list) and 0 < len(targets) <= 100 and len(set(targets)) == len(targets), "Select between 1 and 100 distinct client workspaces.")
        available = {c["target"]: c for c in eligible_clients(snapshot, rule_path)}
        require(all(t in available for t in targets), "Every selected target must be enabled and already assigned this rule.")
        targets = sorted(targets)
        workflow = self.read_workflow(snapshot, repository)
        batches = input_batches(workflow, targets, rule_path, mode=mode)
        info = self.request("GET", "repos/" + repository + "/actions/workflows/" + WORKFLOW)
        require(info["path"] == WORKFLOW_PATH and info["state"] == "active", "The preview workflow is not active.")
        rule = next(r for r in snapshot["rules"] if r["path"] == rule_path)
        clients = []
        for target in targets:
            client = available[target]
            overrides = next(a.get("overrides", {}) for a in client["assignments"] if a["path"] == rule_path)
            azure = client["raw"]["azure"]
            clients.append(dict(target=target, name=client["name"], workspace=client["workspace"],
                rule_state=state(overrides.get("enabled", rule["raw"].get("enabled"))), overrides=copy.deepcopy(overrides),
                subscription_id=azure.get("subscription_id", "Not specified"), resource_group=azure.get("resource_group", "Not specified")))
        plan = dict(repository=repository, repository_id=snapshot["repository_id"], revision=revision,
                    identity=snapshot["identity"], branch="main", workflow_id=info["id"],
                    rule_path=rule_path, targets=targets, batches=batches,
                    clients=clients)
        return dict(version=1, request_id=uuid.uuid4().hex, plan=plan, fingerprint=fingerprint(plan), attempted=False,
                    created_at=datetime.now(timezone.utc).isoformat(), results=[])

    def save(self, record, folder):
        require(re.fullmatch(r"[0-9a-f]{32}", record["request_id"]), "Invalid workflow request ID.")
        folder = Path(folder); folder.mkdir(parents=True, exist_ok=True)
        atomic_write(folder / (record["request_id"] + self.receipt_suffix), json.dumps(record, indent=2) + "\n")

    def verify(self, plan):
        prefix = "repos/" + plan["repository"]
        metadata = self.request("GET", prefix)
        require(metadata.get("private") is True and metadata["id"] == plan["repository_id"] and
                metadata.get("permissions", {}).get("push") is True and metadata["default_branch"] == "main", "Repository identity, access or default branch changed. Prepare again.")
        require(self.request("GET", "user")["login"] == plan["identity"], "GitHub account changed. Prepare again.")
        require(self.request("GET", prefix + "/commits/main")["sha"] == plan["revision"], "Main changed after review. Refresh and prepare again; no further batches were sent.")
        info = self.request("GET", prefix + "/actions/workflows/" + WORKFLOW)
        require(info["path"] == WORKFLOW_PATH and info["id"] == plan["workflow_id"] and info["state"] == "active", "Workflow changed or was disabled. Prepare again.")

    def validate_dispatch(self, plan):
        require(plan["branch"] == "main" and all(b["mode"] == "preview" for b in plan["batches"]), "Only preview requests from main are supported.")

    def dispatch(self, record, folder):
        plan = record["plan"]
        require(record["fingerprint"] == fingerprint(plan), "Workflow request changed. Prepare again.")
        require(not record["attempted"], "This request was already attempted. Check its run results before creating another.")
        self.validate_dispatch(plan)
        self.verify(plan)
        # Persist intention before any dispatch; an ambiguous response must never be retried automatically.
        record["attempted"] = True
        record["results"] = [dict(inputs=copy.deepcopy(b), state="Not submitted") for b in plan["batches"]]
        self.save(record, folder)
        for result in record["results"]:
            self.verify(plan)
            result["state"] = "Submission unconfirmed"
            self.save(record, folder)
            response = self.request("POST", "repos/" + plan["repository"] + "/actions/workflows/" + WORKFLOW + "/dispatches",
                                    {"ref": "main", "inputs": result["inputs"]})
            if response is None:
                result["state"] = "Accepted; run ID unavailable"
                self.save(record, folder)
                continue
            run_id = response.get("workflow_run_id")
            require(type(run_id) is int and run_id > 0, "Dispatch response omitted a valid run ID. Check Actions before trying again.")
            result.update(run_id=run_id, url="https://github.com/" + plan["repository"] + "/actions/runs/" + str(run_id), state="Submitted; status not checked")
            self.save(record, folder)
        return record

    def read_run(self, plan, run_id):
        run = self.request("GET", "repos/" + plan["repository"] + "/actions/runs/" + str(run_id))
        require(run["id"] == run_id and run["workflow_id"] == plan["workflow_id"] and
                run["event"] == "workflow_dispatch" and run["head_branch"] == "main" and
                run["repository"]["id"] == plan["repository_id"], "Returned run does not match this workflow request.")
        return run

    def refresh(self, record, folder):
        plan = record["plan"]
        require(record["fingerprint"] == fingerprint(plan), "Saved workflow request changed.")
        for result in record["results"]:
            if not result.get("run_id"): continue
            run = self.read_run(plan, result["run_id"])
            result["state"] = (run.get("conclusion") or "completed") if run["status"] == "completed" else run["status"]
            result["actual_revision"] = run["head_sha"]
            result["run_status"] = run["status"]
            result["conclusion"] = run.get("conclusion")
            result["checked_at"] = datetime.now(timezone.utc).isoformat()
            if run["head_sha"] != plan["revision"]:
                result["state"] += " — main changed before GitHub started this run"
        self.save(record, folder)
        return record
