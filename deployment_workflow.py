"""Deployment requests tied to successful previews of the same rule and clients."""
import copy

from preview_workflow import PreviewService
from repository_reviews import fingerprint, require


def preview_ready(record):
    """UI hint only. The service checks the actual runs again before any dispatch."""
    if not record or not record.get("attempted"):
        return False
    results, plan = record.get("results", []), record["plan"]
    return bool(results) and len(results) == len(plan["batches"]) and all(
        r.get("run_id") and r.get("run_status") == "completed" and r.get("conclusion") == "success"
        and r.get("actual_revision") == plan["revision"] for r in results)


class DeploymentService(PreviewService):
    receipt_suffix = ".deployment.json"

    def check_preview(self, record):
        plan = record["plan"]
        require(record["fingerprint"] == fingerprint(plan), "The preview request changed. Run a new preview.")
        require(record.get("attempted") is True and plan["batches"] and
                all(b.get("mode") == "preview" for b in plan["batches"]), "A submitted preview is required.")
        results = record["results"]
        require(len(results) == len(plan["batches"]), "Every preview batch must have a confirmed run.")
        run_ids = set()
        for batch, result in zip(plan["batches"], results):
            run_id = result.get("run_id")
            require(type(run_id) is int and run_id > 0 and run_id not in run_ids and result.get("inputs") == batch,
                    "Every preview batch must have its own confirmed run for the reviewed inputs. Check Actions.")
            run_ids.add(run_id)
            run = self.read_run(plan, run_id)
            require(run["status"] == "completed" and run.get("conclusion") == "success",
                    f"Preview run {run_id} has not completed successfully. Deployment was not submitted.")
            require(run["head_sha"] == plan["revision"],
                    "A preview ran a different revision. Refresh the catalog and run new previews.")

    def prepare(self, preview):
        preview = copy.deepcopy(preview)
        self.check_preview(preview)
        original = preview["plan"]
        # Re-read the catalog and workflow. No deployment requests are sent here.
        record = super().prepare(original["repository"], original["revision"], original["rule_path"],
                                 original["targets"], mode="deploy")
        comparable = copy.deepcopy(record["plan"])
        for batch in comparable["batches"]:
            batch["mode"] = "preview"
        require(comparable == original, "The repository, account, workflow or client assignments changed. Run new previews.")
        record["plan"]["preview"] = preview
        record["fingerprint"] = fingerprint(record["plan"])
        self.verify(record["plan"])
        return record

    def validate_dispatch(self, plan):
        require(plan["branch"] == "main" and plan["batches"] and
                all(b.get("mode") == "deploy" for b in plan["batches"]), "Only reviewed deployment requests are supported.")
        proof = plan.get("preview")
        require(isinstance(proof, dict), "Deployment needs its original preview request.")
        comparable = copy.deepcopy(plan)
        del comparable["preview"]
        for batch in comparable["batches"]:
            batch["mode"] = "preview"
        require(comparable == proof["plan"], "Deployment must use exactly the previewed rule, revision and clients.")

    def verify(self, plan):
        self.validate_dispatch(plan)
        # A rerun, cancelled preview, changed account or changed main invalidates
        # approval, including between batches. Local cached success is insufficient.
        self.check_preview(plan["preview"])
        super().verify(plan)
