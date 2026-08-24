from repopilot.evaluation import (
    EvaluationCase,
    EvaluationResult,
    score,
    terminal_summary,
)


def test_score_calculates_provider_neutral_metrics() -> None:
    cases = [
        EvaluationCase("a", "x", "q", "g", "tool", "b", False),
        EvaluationCase("b", "x", "q", "g", None, "b", False),
    ]
    results = [
        EvaluationResult("a", True, True, False, "tool", 1, 100),
        EvaluationResult("b", False, True, True, None, 0, 300, "unsupported"),
    ]
    summary = score(cases, results)
    assert summary["answer_accuracy"] == 0.5
    assert summary["tool_selection_accuracy"] == 1.0
    assert summary["error_count"] == 1
    assert "accuracy=50.0%" in terminal_summary(summary)
