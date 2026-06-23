#!/usr/bin/env python
"""Compare model configurations by running generation on a fixed blueprint N times
under each configuration and reporting cost + quality metrics.

Usage:
    cd demaestro/backend
    python scripts/compare_models.py --blueprint scripts/sample_blueprint.json --runs 2

Configurations (edit CONFIGS dict to taste):
    A: All Sonnet 4.6  (cost baseline)
    B: Generator + Debugger on Opus 4.8  (current production default)
    C: All Opus 4.8  (quality ceiling — expensive)
"""
import argparse
import json
import os
import statistics
import sys
import time
import uuid
from pathlib import Path

# Allow running directly without installing as a package
_BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(_BACKEND_DIR))

CONFIGS: dict[str, dict[str, str]] = {
    "A_all_sonnet": {
        "DEMAESTRO_MODEL_ARCHITECT":  "claude-sonnet-4-6",
        "DEMAESTRO_MODEL_GENERATOR":  "claude-sonnet-4-6",
        "DEMAESTRO_MODEL_DEBUGGER":   "claude-sonnet-4-6",
    },
    "B_gen_dbg_opus48": {
        "DEMAESTRO_MODEL_ARCHITECT":  "claude-sonnet-4-6",
        "DEMAESTRO_MODEL_GENERATOR":  "claude-opus-4-8",
        "DEMAESTRO_MODEL_DEBUGGER":   "claude-opus-4-8",
    },
    "C_all_opus48": {
        "DEMAESTRO_MODEL_ARCHITECT":  "claude-opus-4-8",
        "DEMAESTRO_MODEL_GENERATOR":  "claude-opus-4-8",
        "DEMAESTRO_MODEL_DEBUGGER":   "claude-opus-4-8",
    },
}

POLL_INTERVAL = 10   # seconds between status polls
POLL_TIMEOUT  = 900  # 15 min max per generation


def run_generation(blueprint: dict, config_env: dict[str, str], run_label: str) -> dict:
    """Run one full generation pipeline in-process and return its metrics dict."""
    # Apply the model env vars for this run.
    original_env = {}
    for k, v in config_env.items():
        original_env[k] = os.environ.get(k)
        os.environ[k] = v

    try:
        # Import inside the function so env vars are picked up at agent construction.
        from app.config import get_settings
        from app.pipeline.generation_orchestrator import GenerationOrchestrator

        # We need a uid and project_id.  For comparison runs, create a throw-away
        # project directly via the firestore service so we don't need an HTTP server.
        from app.services import firestore_service
        from app.models.project import ProjectMeta, ProjectStatus, StackChoice
        from app.ai.gemini.agents.blueprint import BlueprintResponse
        from app.models.structured_requirements import StructuredRequirements

        uid = blueprint.get("test_uid", "compare_models_test_user")
        project_id = f"cmp_{uuid.uuid4().hex[:8]}"

        # Create a minimal project doc
        from datetime import datetime, timezone
        meta = ProjectMeta(
            id=project_id,
            name=f"compare_{run_label}",
            status=ProjectStatus.generating,
        )
        firestore_service.create_project(uid, meta)

        # Store the structured requirements from the blueprint
        sr_data = blueprint.get("structured_requirements", {})
        if sr_data:
            sr = StructuredRequirements(**sr_data)
            firestore_service.add_structured_requirements(uid, project_id, sr)

        # Store the blueprint response
        bp_data = blueprint.get("blueprint_response", {})
        if bp_data:
            firestore_service.update_project(uid, project_id, {
                "blueprint": bp_data,
                "status": ProjectStatus.approved,
            })

        print(f"    project_id={project_id}, uid={uid}")
        started = time.time()

        orchestrator = GenerationOrchestrator()
        result = orchestrator.run_full_pipeline(uid, project_id)

        elapsed = int(time.time() - started)

        # Read back the project doc to get generation_metrics
        project = firestore_service.get_project(uid, project_id)
        metrics = getattr(project, "generation_metrics", None) or {}
        cost = getattr(project, "estimated_cost_usd", None) or 0.0

        return {
            "run_label": run_label,
            "final_status": "success" if result.get("status") == "success" else "strict_failure",
            "duration_sec": elapsed,
            "cycle_count": metrics.get("cycle_count", 0),
            "estimated_cost_usd": cost,
            "typecheck_warnings": getattr(project, "typecheck_warnings", 0) or 0,
            "contract_misses": getattr(project, "contract_advisory_misses", []) or [],
            "debug_files_touched": metrics.get("debug_files_touched", []),
            "debugger_validator_rejections": metrics.get("debugger_validator_rejections", 0),
            "tokens_by_agent": metrics.get("tokens_by_agent", {}),
            "models_used_by_agent": metrics.get("models_used_by_agent", {}),
            "project_id": project_id,
        }
    finally:
        # Restore original env
        for k, v in original_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare DeMaestro model configurations by cost + quality metrics."
    )
    parser.add_argument("--blueprint", required=True,
                        help="Path to a sample_blueprint.json file")
    parser.add_argument("--runs", type=int, default=2,
                        help="Number of generation runs per configuration")
    parser.add_argument("--configs", nargs="+", choices=list(CONFIGS),
                        help="Subset of configs to test (default: all)")
    parser.add_argument("--output", default="comparison_results.json",
                        help="Output file for the full results JSON")
    args = parser.parse_args()

    blueprint = json.loads(Path(args.blueprint).read_text())
    configs_to_run = {k: CONFIGS[k] for k in (args.configs or CONFIGS)}

    all_runs: dict[str, list[dict]] = {label: [] for label in configs_to_run}

    for config_label, config_env in configs_to_run.items():
        print(f"\n{'='*60}")
        print(f"Config: {config_label}")
        print(f"Models: {json.dumps(config_env, indent=2)}")
        print(f"{'='*60}")
        for run_n in range(args.runs):
            run_label = f"{config_label}_run{run_n}"
            print(f"\n  Run {run_n + 1}/{args.runs} ({run_label})...")
            try:
                metrics = run_generation(blueprint, config_env, run_label)
                all_runs[config_label].append(metrics)
                print(f"    status={metrics['final_status']} "
                      f"cost=${metrics['estimated_cost_usd']:.4f} "
                      f"cycles={metrics['cycle_count']} "
                      f"duration={metrics['duration_sec']}s")
            except Exception as exc:
                print(f"    ERROR: {exc}")
                all_runs[config_label].append({
                    "run_label": run_label, "error": str(exc),
                    "final_status": "error", "estimated_cost_usd": 0,
                    "cycle_count": 0, "duration_sec": 0,
                    "typecheck_warnings": 0, "contract_misses": [],
                })

    # Aggregate results
    report: dict[str, dict] = {}
    for label, runs in all_runs.items():
        valid = [r for r in runs if r.get("final_status") != "error"]
        if not valid:
            report[label] = {"error": "all runs failed", "raw": runs}
            continue
        shipped = [r for r in valid if r["final_status"] == "success"]
        report[label] = {
            "n_runs":                   len(runs),
            "n_shipped":                len(shipped),
            "ship_rate":                len(shipped) / max(1, len(valid)),
            "avg_cycles":               statistics.mean(r["cycle_count"] for r in valid),
            "avg_duration_sec":         statistics.mean(r["duration_sec"] for r in valid),
            "avg_cost_usd":             statistics.mean(r["estimated_cost_usd"] for r in valid),
            "avg_typecheck_warnings":   statistics.mean(r.get("typecheck_warnings", 0) for r in valid),
            "avg_contract_misses":      statistics.mean(len(r.get("contract_misses", [])) for r in valid),
            "avg_validator_rejections": statistics.mean(r.get("debugger_validator_rejections", 0) for r in valid),
            "cost_per_shipped_app":     (
                sum(r["estimated_cost_usd"] for r in valid) / max(1, len(shipped))
            ),
            "models": runs[0].get("models_used_by_agent", {}) if runs else {},
            "raw_runs": runs,
        }

    output_path = Path(args.output)
    output_path.write_text(json.dumps(report, indent=2))

    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    for label, data in report.items():
        if "error" in data:
            print(f"\n{label}: ALL RUNS FAILED")
            continue
        print(f"\n{label}:")
        print(f"  Ship rate:            {data['ship_rate']:.0%} ({data['n_shipped']}/{data['n_runs']})")
        print(f"  Avg cost:             ${data['avg_cost_usd']:.4f}")
        print(f"  Cost per shipped app: ${data['cost_per_shipped_app']:.4f}")
        print(f"  Avg cycles:           {data['avg_cycles']:.1f}")
        print(f"  Avg duration:         {data['avg_duration_sec']:.0f}s")
        print(f"  Avg typecheck warns:  {data['avg_typecheck_warnings']:.0f}")
        print(f"  Avg contract misses:  {data['avg_contract_misses']:.1f}")

    print(f"\nFull results written to {output_path}")


if __name__ == "__main__":
    main()
