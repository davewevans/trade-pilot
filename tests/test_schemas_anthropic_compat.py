"""Guard against reintroducing JSON Schema keywords that the Anthropic
structured outputs API rejects at grammar-compile time.

Context: Anthropic's structured outputs (output_config.format.schema)
compiles the schema into a decoding grammar. Validation keywords like
`minimum`, `maximum`, `pattern`, etc. are not expressible in that
grammar and are rejected with a 400 error.

The Anthropic Python SDK's Pydantic path strips these and folds them
into the field `description`; trade-pilot sends raw dict schemas
straight through, so the keywords must not appear in the dicts at all.

Post-parse validation (guardrails.py for trading schemas,
validate_judge_response for the judge schema) carries any constraints
that used to live in the schema.
"""

import pytest

from ai.schemas import _REGISTRY
from evaluation.judge_schema import JUDGE_OUTPUT_SCHEMA

# Keywords Anthropic structured outputs rejects. Derived from the
# public docs (Pydantic stripping behavior) and the vercel/ai bug
# report https://github.com/vercel/ai/issues/13355. Update cautiously
# if Anthropic formally documents support for any of these.
FORBIDDEN_KEYS = frozenset({
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "minItems",
    "maxItems",
    "minLength",
    "maxLength",
    "pattern",
    "format",
    "multipleOf",
    "uniqueItems",
})


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
        (path, key)
        for path, key in _walk(schema, f"_REGISTRY[{name!r}]")
        if key in FORBIDDEN_KEYS
    ]
    assert not offenders, (
        f"Schema {name!r} contains JSON Schema validation keywords that "
        f"Anthropic structured outputs rejects at grammar-compile time. "
        f"Offenders: {offenders}. "
        f"Remove them from the schema dict and enforce the constraint "
        f"post-parse in guardrails.py instead."
    )


def test_judge_output_schema_has_no_forbidden_keys():
    offenders = [
        (path, key)
        for path, key in _walk(JUDGE_OUTPUT_SCHEMA, "JUDGE_OUTPUT_SCHEMA")
        if key in FORBIDDEN_KEYS
    ]
    assert not offenders, (
        f"JUDGE_OUTPUT_SCHEMA contains JSON Schema validation keywords "
        f"that Anthropic structured outputs rejects: {offenders}. "
        f"validate_judge_response() in evaluation/judge_schema.py carries "
        f"the post-parse checks."
    )
