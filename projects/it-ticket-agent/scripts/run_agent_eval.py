from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from it_ticket_agent.evals import AgentEvalRunner, load_agent_eval_dataset, serialize_report
from it_ticket_agent.evals import (
    SessionFlowEvalRunner,
    load_session_flow_eval_dataset,
    serialize_session_flow_report,
)
from it_ticket_agent.settings import Settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run agent eval cases with real LLM and mocked tool outputs.")
    parser.add_argument(
        "--dataset",
        default=str(PROJECT_ROOT / "data" / "evals" / "tool_mock_cases.json"),
        help="Path to the eval dataset JSON file.",
    )
    parser.add_argument(
        "--profiles",
        default=str(PROJECT_ROOT / "data" / "mock_case_profiles.json"),
        help="Path to the tool profile JSON file used by setup.tool_profile.",
    )
    parser.add_argument(
        "--case-id",
        action="append",
        default=[],
        help="Run only the specified case_id. Can be repeated.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional path to write the JSON report.",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop at the first failed or errored case.",
    )
    parser.add_argument(
        "--rag-enabled",
        action="store_true",
        help="Enable RAG during eval. Disabled by default to keep the run focused on tool use.",
    )
    parser.add_argument(
        "--allow-llm-disabled",
        action="store_true",
        help="Allow running without an active LLM. Mainly useful for local harness debugging.",
    )
    return parser


def print_agent_report(report) -> None:
    for item in report.results:
        label = "ERROR" if item.error else ("PASS" if item.passed else "FAIL")
        tools = ",".join((item.observation.tool_names if item.observation is not None else [])[:4]) or "-"
        stop_reason = item.observation.stop_reason if item.observation is not None else "-"
        expansion = item.observation.expansion_probe_count if item.observation is not None else 0
        rejected = item.observation.rejected_tool_call_count if item.observation is not None else 0
        print(
            f"[{label}] {item.case_id} score={item.score:.3f} "
            f"checks={item.passed_checks}/{item.total_checks} duration_ms={item.duration_ms} "
            f"tools={tools} stop={stop_reason} expand={expansion} rejected={rejected}"
        )
        if item.error:
            print(f"  error: {item.error}")
            continue
        failed_checks = [check for check in item.checks if not check.passed]
        for check in failed_checks[:4]:
            detail = f" detail={check.detail}" if check.detail else ""
            print(
                f"  check={check.name} expected={json.dumps(check.expected, ensure_ascii=False)} "
                f"actual={json.dumps(check.actual, ensure_ascii=False)}{detail}"
            )
    print(
        "summary: "
        f"total={report.total_cases} passed={report.passed_cases} "
        f"failed={report.failed_cases} errored={report.errored_cases} pass_rate={report.pass_rate:.3f}"
    )
    print(
        "metrics: "
        f"avg_tool_calls={report.avg_tool_calls_used:.3f} avg_duration_ms={report.avg_duration_ms:.3f} "
        f"expansion_probe_cases={report.expansion_probe_cases} "
        f"rejected_tool_call_cases={report.rejected_tool_call_cases} "
        f"rejected_tool_call_total={report.rejected_tool_call_total} "
        f"stop_reasons={json.dumps(report.stop_reason_counts, ensure_ascii=False, sort_keys=True)}"
    )


def print_session_flow_report(report) -> None:
    for item in report.results:
        label = "ERROR" if item.error else ("PASS" if item.passed else "FAIL")
        print(f"[{label}] {item.case_id} steps={len(item.step_results)} duration_ms={item.duration_ms}")
        if item.error:
            print(f"  error: {item.error}")
            continue
        for step in item.step_results:
            step_label = "ERROR" if step.error else ("PASS" if step.passed else "FAIL")
            interrupt_type = step.observation.pending_interrupt_type if step.observation is not None else "-"
            stop_reason = step.observation.stop_reason if step.observation is not None else "-"
            print(
                f"  [{step_label}] {step.step_id or step.action} action={step.action} "
                f"checks={step.passed_checks}/{step.total_checks} "
                f"status={(step.observation.response_status if step.observation is not None else '-')} "
                f"interrupt={interrupt_type} stop={stop_reason}"
            )
            if step.error:
                print(f"    error: {step.error}")
                continue
            failed_checks = [check for check in step.checks if not check.passed]
            for check in failed_checks[:4]:
                detail = f" detail={check.detail}" if check.detail else ""
                print(
                    f"    check={check.name} expected={json.dumps(check.expected, ensure_ascii=False)} "
                    f"actual={json.dumps(check.actual, ensure_ascii=False)}{detail}"
                )
    print(
        "summary: "
        f"total={report.total_cases} passed={report.passed_cases} "
        f"failed={report.failed_cases} errored={report.errored_cases} pass_rate={report.pass_rate:.3f}"
    )
    print(
        "metrics: "
        f"total_steps={report.total_steps} passed_steps={report.passed_steps} "
        f"step_pass_rate={report.step_pass_rate:.3f} avg_duration_ms={report.avg_duration_ms:.3f}"
    )


def detect_dataset_mode(dataset_path: str) -> str:
    payload = json.loads(Path(dataset_path).read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        cases = payload.get("cases")
        if isinstance(cases, list) and cases and isinstance(cases[0], dict) and "steps" in cases[0]:
            return "session_flow"
    if isinstance(payload, list) and payload and isinstance(payload[0], dict) and "steps" in payload[0]:
        return "session_flow"
    return "agent"


async def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    dataset_mode = detect_dataset_mode(args.dataset)
    settings = Settings()
    if dataset_mode == "session_flow":
        dataset = load_session_flow_eval_dataset(args.dataset)
        runner = SessionFlowEvalRunner(
            settings,
            profiles_path=args.profiles,
            rag_enabled=args.rag_enabled,
            require_llm_enabled=not args.allow_llm_disabled,
        )
        report = await runner.run_dataset(
            dataset,
            selected_case_ids=args.case_id,
            fail_fast=args.fail_fast,
        )
    else:
        dataset = load_agent_eval_dataset(args.dataset)
        runner = AgentEvalRunner(
            settings,
            profiles_path=args.profiles,
            rag_enabled=args.rag_enabled,
            require_llm_enabled=not args.allow_llm_disabled,
        )
        report = await runner.run_dataset(
            dataset,
            selected_case_ids=args.case_id,
            fail_fast=args.fail_fast,
        )
    if report.total_cases == 0:
        print("no eval cases selected")
        return 2
    print(
        f"mode={dataset_mode} model={settings.llm_model or '-'} "
        f"base_url={settings.llm_base_url or '-'} rag_enabled={args.rag_enabled}"
    )
    if dataset_mode == "session_flow":
        print_session_flow_report(report)
    else:
        print_agent_report(report)
    if args.output:
        serialized = (
            serialize_session_flow_report(report)
            if dataset_mode == "session_flow"
            else serialize_report(report)
        )
        Path(args.output).write_text(json.dumps(serialized, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report.failed_cases == 0 and report.errored_cases == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
