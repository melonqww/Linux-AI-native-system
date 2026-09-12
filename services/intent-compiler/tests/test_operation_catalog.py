from types import SimpleNamespace

import pytest

from ai_native_intents import (
    CallableIntentProvider,
    CompilationState,
    IntentCompiler,
    OperationCatalog,
    OperationDefinition,
    RiskClass,
    build_operation_definitions,
)


def contract(capability="notes.lookup", operation="lookup_notes"):
    return SimpleNamespace(
        capability_id=capability,
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string", "minLength": 1}},
            "required": ["query"],
            "additionalProperties": False,
        },
        user_intent=SimpleNamespace(
            operation=operation,
            description="Search the user's local notes.",
            examples=("find my meeting notes",),
            preserved_arguments=("query",),
        ),
    )


def policy(_capability):
    return SimpleNamespace(
        risk=SimpleNamespace(value="R1"),
        plan_approval_required=True,
        allowed_arguments=frozenset({"query"}),
        required_arguments=frozenset({"query"}),
    )


def test_new_module_operation_flows_from_contract_to_schema_validation_and_plan():
    definitions = build_operation_definitions(
        (contract(),),
        available_capabilities=("notes.lookup",),
        policy_source=policy,
    )
    text = "find my meeting notes"
    response = {
        "schema_version": 1,
        "language": "en",
        "summary": text,
        "confidence": 0.9,
        "operations": [
            {
                "id": "lookup",
                "kind": "lookup_notes",
                "arguments": {"query": "meeting"},
                "depends_on": [],
                "evidence": [text],
            }
        ],
    }
    captured = []
    compiler = IntentCompiler(
        CallableIntentProvider(lambda request: captured.append(request) or response),
        operation_source=lambda: definitions,
    )

    result = compiler.compile_and_plan(text)

    assert result.state is CompilationState.READY
    assert result.plan.steps[0].capability == "notes.lookup"
    assert result.plan.steps[0].risk is RiskClass.REVERSIBLE_WRITE
    assert result.plan.steps[0].approval_required is True
    variants = captured[0].output_schema["properties"]["operations"]["items"]["oneOf"]
    assert variants[0]["properties"]["kind"]["const"] == "lookup_notes"
    assert captured[0].operation_definitions == definitions
    assert definitions[0].preserved_arguments == ("query",)


def test_catalog_excludes_non_executable_contracts_and_rejects_unknown_arguments():
    definitions = build_operation_definitions(
        (contract(), contract("notes.delete", "delete_notes")),
        available_capabilities=("notes.lookup",),
        policy_source=policy,
    )
    assert [item.operation for item in definitions] == ["lookup_notes"]
    with pytest.raises(ValueError, match="unsupported arguments"):
        definitions[0].validate_arguments({"query": "meeting", "shell": "rm -rf /"})


def test_catalog_rejects_duplicate_operations_and_unsafe_open_schema():
    first = OperationDefinition(
        "lookup_notes",
        "notes.lookup",
        "Lookup notes.",
        {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    )
    second = OperationDefinition(
        "lookup_notes",
        "notes.search",
        "Search notes.",
        {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    )
    with pytest.raises(ValueError, match="duplicate operations"):
        OperationCatalog((first, second))
    with pytest.raises(ValueError, match="closed object"):
        OperationDefinition(
            "unsafe",
            "notes.unsafe",
            "Unsafe schema.",
            {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": True,
            },
        )


def test_catalog_rejects_unknown_preserved_argument():
    with pytest.raises(ValueError, match="must name operation input properties"):
        OperationDefinition(
            "lookup_notes",
            "notes.lookup",
            "Lookup notes.",
            {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
            preserved_arguments=("missing",),
        )


def test_catalog_enforces_and_hides_conditional_argument_contract():
    definition = OperationDefinition(
        "lookup_notes",
        "notes.lookup",
        "Lookup notes.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "match": {
                    "type": "string",
                    "enum": ["semantic", "exact"],
                    "coRequiredWith": ["query"],
                    "reviewChoices": {
                        "semantic": ["semantic", "topic", "content_topic"],
                        "exact": ["exact", "phrase", "literal_content_phrase"],
                        "$misplaced": ["misplaced", "destination", "file_type", "other_argument"],
                    },
                },
            },
            "required": [],
            "additionalProperties": False,
        },
    )

    assert definition.missing_corequired_arguments({"query": "meeting"}) == (
        "match",
    )
    with pytest.raises(ValueError, match="conditionally required"):
        definition.validate_arguments({"query": "meeting"})
    with pytest.raises(ValueError, match="requires one of"):
        definition.validate_arguments({"match": "semantic"})
    assert definition.validate_arguments(
        {"query": "meeting", "match": "semantic"}
    ) == {"query": "meeting", "match": "semantic"}
    assert "coRequiredWith" not in definition.model_input_schema["properties"]["match"]
    assert "reviewChoices" not in definition.model_input_schema["properties"]["match"]


def test_catalog_rejects_invalid_conditional_argument_reference():
    with pytest.raises(ValueError, match="must name other operation properties"):
        OperationDefinition(
            "lookup_notes",
            "notes.lookup",
            "Lookup notes.",
            {
                "type": "object",
                "properties": {
                    "match": {
                        "type": "string",
                        "coRequiredWith": ["missing"],
                    }
                },
                "required": [],
                "additionalProperties": False,
            },
        )


def test_module_schema_cannot_expand_trusted_policy_arguments():
    def restrictive(_capability):
        return SimpleNamespace(
            risk=SimpleNamespace(value="R0"),
            plan_approval_required=False,
            allowed_arguments=frozenset(),
            required_arguments=frozenset(),
        )

    with pytest.raises(ValueError, match="conflicts with trusted policy"):
        build_operation_definitions(
            (contract(),),
            available_capabilities=("notes.lookup",),
            policy_source=restrictive,
        )
