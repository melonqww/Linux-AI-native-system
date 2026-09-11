from ai_scenario_lab.diagnostics import (
    DiagnosticContext,
    ProblemLayer,
    classify_problem,
)


def _context(**changes):
    values = {
        "language": "ru",
        "behavior": "typos",
        "mode": "action",
        "memory_depth": 5,
        "capability": "storage.search",
        "decision": "none",
        "failure_kind": "timeout",
        "input_modality": "text",
    }
    values.update(changes)
    return DiagnosticContext(**values)


def test_classifier_uses_structured_evidence_and_defaults_to_unknown():
    assert (
        classify_problem(_context(), {"message": "model timeout"}).layer
        is ProblemLayer.UNKNOWN
    )
    assert (
        classify_problem(_context(), {"code": "model_timeout"}).layer
        is ProblemLayer.MODEL
    )
    assert (
        classify_problem(_context(), {"fault_point": "executor.copy"}).layer
        is ProblemLayer.EXECUTOR
    )


def test_safety_evidence_has_priority_over_lower_layer_failure():
    diagnostic = classify_problem(
        _context(),
        {"code": "model_timeout", "containment_passed": False},
    )
    assert diagnostic.layer is ProblemLayer.CONTAINMENT
    assert diagnostic.rule == "containment_passed_false"


def test_each_requested_problem_layer_has_a_deterministic_rule():
    cases = {
        "route_mismatch": ProblemLayer.ROUTER,
        "intent_not_recognized": ProblemLayer.ROUTER,
        "invalid_plan": ProblemLayer.COMPILER,
        "approval_bypass": ProblemLayer.POLICY,
        "execution_failed": ProblemLayer.EXECUTOR,
        "index_stale": ProblemLayer.INDEX,
        "outside_write": ProblemLayer.CONTAINMENT,
        "malformed_model_output": ProblemLayer.MODEL,
        "something_new": ProblemLayer.UNKNOWN,
    }
    for code, expected in cases.items():
        assert classify_problem(_context(), {"code": code}).layer is expected


def test_compiler_validation_code_is_structurally_classified():
    diagnostic = classify_problem(
        _context(),
        {
            "code": "invalid_arguments",
            "component": "intent_compiler",
            "stage": "validation",
            "message": "must never enter a report",
        },
    )
    assert diagnostic.layer is ProblemLayer.COMPILER
    assert diagnostic.evidence == {
        "code": "invalid_arguments",
        "stage": "validation",
        "component": "intent_compiler",
    }


def test_fingerprint_ignores_volatile_prose_but_separates_real_dimensions():
    first = classify_problem(
        _context(), {"code": "model_timeout", "message": "one", "timestamp": 1}
    )
    same = classify_problem(
        _context(), {"code": "model_timeout", "message": "two", "timestamp": 2}
    )
    english = classify_problem(_context(language="en"), {"code": "model_timeout"})
    deep = classify_problem(_context(memory_depth=35), {"code": "model_timeout"})
    assert first.fingerprint == same.fingerprint
    assert len({first.fingerprint, english.fingerprint, deep.fingerprint}) == 3


def test_context_rejects_invalid_memory_depth():
    try:
        _context(memory_depth=-1)
    except ValueError as error:
        assert "memory_depth" in str(error)
    else:
        raise AssertionError("negative memory depth was accepted")


def test_context_rejects_non_integer_memory_depth():
    try:
        _context(memory_depth="5")
    except ValueError as error:
        assert "memory_depth" in str(error)
    else:
        raise AssertionError("text memory depth was accepted")
