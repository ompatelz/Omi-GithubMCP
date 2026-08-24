"""Deterministic scoring for manually or provider-recorded RepoPilot evaluations."""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class EvaluationCase:
    id: str
    category: str
    question: str
    ground_truth: str
    expected_tool: str | None
    expected_behavior: str
    allows_write: bool


@dataclass(frozen=True)
class EvaluationResult:
    case_id: str
    answer_correct: bool
    completed: bool
    hallucinated: bool
    selected_tool: str | None
    tool_calls: int
    latency_ms: float
    error: str | None = None


def load_cases(path: Path) -> list[EvaluationCase]:
    """Load machine-readable cases without contacting an external provider."""
    return [EvaluationCase(**item) for item in json.loads(path.read_text("utf-8"))]


def score(
    cases: list[EvaluationCase], results: list[EvaluationResult]
) -> dict[str, object]:
    """Calculate reproducible aggregate metrics for recorded model outcomes."""
    by_id = {case.id: case for case in cases}
    if len(results) != len(by_id) or {result.case_id for result in results} != set(
        by_id
    ):
        raise ValueError(
            "results must contain exactly one entry for every evaluation case"
        )
    total = len(results)
    selected = sum(
        result.selected_tool == by_id[result.case_id].expected_tool
        for result in results
    )
    return {
        "case_count": total,
        "answer_accuracy": _rate(
            sum(result.answer_correct for result in results), total
        ),
        "task_completion_rate": _rate(
            sum(result.completed for result in results), total
        ),
        "hallucination_rate": _rate(
            sum(result.hallucinated for result in results), total
        ),
        "tool_selection_accuracy": _rate(selected, total),
        "average_tool_calls": sum(result.tool_calls for result in results) / total,
        "average_latency_ms": sum(result.latency_ms for result in results) / total,
        "error_count": sum(result.error is not None for result in results),
    }


def write_json(path: Path, summary: dict[str, object]) -> None:
    """Persist a provider-neutral summary for later comparison."""
    path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")


def write_csv(path: Path, summary: dict[str, object]) -> None:
    """Write one flat summary row for spreadsheet comparison."""
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=list(summary))
        writer.writeheader()
        writer.writerow(summary)


def terminal_summary(summary: dict[str, object]) -> str:
    """Render a concise deterministic summary for manual experiments."""
    return (
        f"cases={summary['case_count']} accuracy={summary['answer_accuracy']:.1%} "
        f"completion={summary['task_completion_rate']:.1%} "
        f"hallucination={summary['hallucination_rate']:.1%} "
        f"tool_selection={summary['tool_selection_accuracy']:.1%} "
        f"errors={summary['error_count']}"
    )


def result_record(result: EvaluationResult) -> dict[str, object]:
    """Return a JSON-ready result record for manual collection workflows."""
    return asdict(result)


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0
