"""Guard against reintroducing JSON Schema keywords that the Anthropic
structured outputs API rejects."""
import pytest

from ai.schemas import _REGISTRY
from evaluation.judge_schema import JUDGE_OUTPUT_SCHEMA

FORBIDDEN_KEYS = {
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
    "minItems", "maxItems", "minLength", "maxLength",
    "pattern", "format", "multipleOf", "uniqueItems",
}


def _walk(node, path="$"):
    """Yield (path, key) for every dict key in the schema tree."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield path, k
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk(v, f"{path}[{i}]")


@pytest.mark.parametrize("name,schema", sorted(_REGISTRY.items()))
def test_registry_schema_has_no_forbidden_keys(name, schema):
    offenders = [
        (path, key) for path, key in _walk(schema, f"_REGISTRY[{name!r}]")
        if key in FORBIDDEN_KEYS
    ]
    assert not offenders, (
        f"Schema {name!r} contains keywords Anthropic structured outputs "
        f"rejects: {offenders}"
    )


def test_judge_output_schema_has_no_forbidden_keys():
    offenders = [
        (path, key) for path, key
        in _walk(JUDGE_OUTPUT_SCHEMA, "JUDGE_OUTPUT_SCHEMA")
        if key in FORBIDDEN_KEYS
    ]
    assert not offenders, (
        f"JUDGE_OUTPUT_SCHEMA contains forbidden keywords: {offenders}"
    )
