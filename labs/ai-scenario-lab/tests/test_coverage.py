import pytest

from ai_scenario_lab.coverage import CoverageObservation, coverage_taxonomy
from ai_scenario_lab.diagnostics import DiagnosticContext


def _observation(language, depth, passed, scenario):
    return CoverageObservation(
        DiagnosticContext(
            language=language,
            behavior="ordinary",
            mode="chat",
            memory_depth=depth,
            capability="conversation.memory",
            decision="none",
            failure_kind="semantic" if not passed else "none",
            input_modality="text",
        ),
        passed,
        scenario,
    )


def test_taxonomy_keeps_language_and_memory_depth_separate():
    report = coverage_taxonomy(
        (
            _observation("ru", 5, False, "ru-short"),
            _observation("en", 5, True, "en-short"),
            _observation("ru", 35, True, "ru-long"),
        ),
        intersections=(("language", "memory_depth"),),
    )
    assert report["attempts"] == 3
    language = {row["language"]: row for row in report["axes"]["language"]}
    assert language["ru"]["pass_rate"] == 0.5
    assert language["en"]["pass_rate"] == 1.0
    cross = report["intersections"]["language × memory_depth"]
    assert {(row["language"], row["memory_depth"]) for row in cross} == {
        ("ru", 5),
        ("en", 5),
        ("ru", 35),
    }


def test_repeated_attempts_report_counts_and_scenario_diversity():
    report = coverage_taxonomy(
        (
            _observation("ru", 5, True, "memory"),
            _observation("ru", 5, False, "memory"),
        )
    )
    row = report["axes"]["language"][0]
    assert row == {
        "language": "ru",
        "attempts": 2,
        "passed": 1,
        "failed": 1,
        "pass_rate": 0.5,
        "scenario_count": 1,
    }


def test_unknown_intersection_dimension_is_rejected():
    with pytest.raises(ValueError, match="unknown"):
        coverage_taxonomy((), intersections=(("language", "weather"),))
