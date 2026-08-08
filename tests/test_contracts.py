"""Both sides of every call are checked, and neither side can be skipped."""
from __future__ import annotations

import pytest

from toolwright.contracts import ContractError, Registry, Tool, to_mcp_result, validate

QUOTE_PARAMS = {
    "type": "object",
    "properties": {
        "symbol": {"type": "string", "pattern": "^[A-Z]{1,5}$"},
        "quantity": {"type": "integer", "minimum": 1, "maximum": 10_000},
        "side": {"type": "string", "enum": ["buy", "sell"]},
    },
    "required": ["symbol", "quantity", "side"],
    "additionalProperties": False,
}

QUOTE_RETURNS = {
    "type": "object",
    "properties": {
        "symbol": {"type": "string"},
        "total_cents": {"type": "integer", "minimum": 0},
        "side": {"type": "string", "enum": ["buy", "sell"]},
    },
    "required": ["symbol", "total_cents", "side"],
    "additionalProperties": False,
}


def good_handler(symbol: str, quantity: int, side: str) -> dict:
    return {"symbol": symbol, "total_cents": quantity * 1234, "side": side}


def registry(handler=good_handler) -> Registry:
    r = Registry()
    r.register(
        Tool(
            name="quote",
            description="Price an order",
            parameters=QUOTE_PARAMS,
            returns=QUOTE_RETURNS,
            handler=handler,
        )
    )
    return r


# -- inbound --------------------------------------------------------------


def test_a_valid_call_succeeds():
    r = registry().call("quote", {"symbol": "AAPL", "quantity": 5, "side": "buy"})
    assert r.ok
    assert r.value["total_cents"] == 6170


def test_a_missing_required_argument_is_refused():
    r = registry().call("quote", {"symbol": "AAPL", "quantity": 5})
    assert not r.ok
    assert any("side" in v.path and "required" in v.message for v in r.violations)


def test_a_wrong_type_is_refused():
    r = registry().call("quote", {"symbol": "AAPL", "quantity": "five", "side": "buy"})
    assert not r.ok
    assert any("expected integer" in v.message for v in r.violations)


def test_a_boolean_is_not_accepted_as_a_number():
    """bool subclasses int in Python, so this slips through a naive isinstance."""
    r = registry().call("quote", {"symbol": "AAPL", "quantity": True, "side": "buy"})
    assert not r.ok
    assert any("got boolean" in v.message for v in r.violations)


def test_an_out_of_range_value_is_refused():
    r = registry().call("quote", {"symbol": "AAPL", "quantity": 0, "side": "buy"})
    assert not r.ok
    assert any(">= 1" in v.message for v in r.violations)


def test_an_enum_violation_is_refused():
    r = registry().call("quote", {"symbol": "AAPL", "quantity": 5, "side": "hodl"})
    assert not r.ok
    assert any("must be one of" in v.message for v in r.violations)


def test_a_pattern_violation_is_refused():
    r = registry().call("quote", {"symbol": "not-a-ticker", "quantity": 5, "side": "buy"})
    assert not r.ok
    assert any("must match" in v.message for v in r.violations)


def test_an_unknown_argument_is_refused_not_dropped():
    """THE inbound invariant. Silently discarding an argument the model believed
    it was passing makes the call do something other than what was asked."""
    r = registry().call(
        "quote", {"symbol": "AAPL", "quantity": 5, "side": "buy", "limit_price": 100}
    )
    assert not r.ok
    assert any("limit_price" in v.path for v in r.violations)


def test_the_handler_does_not_run_when_arguments_are_invalid():
    calls = []

    def spy(**kwargs):
        calls.append(kwargs)
        return {"symbol": "X", "total_cents": 0, "side": "buy"}

    registry(spy).call("quote", {"symbol": "AAPL"})
    assert calls == [], "handler ran on unvalidated arguments"


def test_non_object_arguments_are_refused():
    r = registry().call("quote", ["AAPL", 5, "buy"])
    assert not r.ok
    assert any("expected object" in v.message for v in r.violations)


def test_an_unknown_tool_is_refused():
    assert registry().call("nope", {}).error.startswith("unknown tool")


# -- outbound (the half most implementations skip) ------------------------


def test_a_tool_that_breaks_its_return_contract_fails():
    """THE outbound invariant. Without this the bad shape reaches the model and
    the failure surfaces somewhere unrelated."""

    def wrong(symbol, quantity, side):
        return {"symbol": symbol, "total_cents": "quite a lot", "side": side}

    r = registry(wrong).call("quote", {"symbol": "AAPL", "quantity": 5, "side": "buy"})
    assert not r.ok
    assert any(v.where == "result" for v in r.violations)
    assert any("expected integer" in v.message for v in r.violations)


def test_a_tool_returning_an_extra_field_fails():
    def chatty(symbol, quantity, side):
        return {
            "symbol": symbol,
            "total_cents": 1,
            "side": side,
            "internal_account": "acct_99",
        }

    r = registry(chatty).call("quote", {"symbol": "AAPL", "quantity": 5, "side": "buy"})
    assert not r.ok
    assert any("internal_account" in v.path for v in r.violations)


def test_a_tool_returning_a_negative_where_a_minimum_is_declared_fails():
    def negative(symbol, quantity, side):
        return {"symbol": symbol, "total_cents": -1, "side": side}

    r = registry(negative).call("quote", {"symbol": "AAPL", "quantity": 5, "side": "buy"})
    assert not r.ok
    assert any(v.where == "result" for v in r.violations)


def test_a_tool_returning_the_wrong_container_fails():
    def listy(symbol, quantity, side):
        return [symbol, quantity, side]

    r = registry(listy).call("quote", {"symbol": "AAPL", "quantity": 5, "side": "buy"})
    assert not r.ok
    assert any("expected object" in v.message for v in r.violations)


def test_a_raising_tool_is_a_failed_call_not_a_result():
    def boom(**_):
        raise RuntimeError("upstream timeout")

    r = registry(boom).call("quote", {"symbol": "AAPL", "quantity": 5, "side": "buy"})
    assert not r.ok
    assert r.value is None
    assert "upstream timeout" in r.error


# -- declarations ---------------------------------------------------------


def test_additional_properties_defaults_to_closed():
    """Forgetting to close a contract on a tool an LLM can call must be safe."""
    t = Tool(
        name="t",
        description="",
        parameters={"type": "object", "properties": {"a": {"type": "string"}}},
        returns={"type": "string"},
        handler=lambda a: a,
    )
    assert t.parameters["additionalProperties"] is False


def test_a_non_object_parameter_schema_is_refused():
    with pytest.raises(ContractError):
        Tool(name="t", description="", parameters={"type": "string"},
             returns={"type": "string"}, handler=lambda: "")


def test_a_duplicate_tool_name_is_refused():
    r = registry()
    with pytest.raises(ContractError):
        r.register(Tool(name="quote", description="", parameters=QUOTE_PARAMS,
                        returns=QUOTE_RETURNS, handler=good_handler))


def test_an_unknown_schema_type_is_a_contract_error():
    with pytest.raises(ContractError):
        validate("x", {"type": "sting"}, "arguments")


# -- nesting and MCP shape ------------------------------------------------


def test_nested_objects_and_arrays_are_validated():
    schema = {
        "type": "object",
        "properties": {
            "orders": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"symbol": {"type": "string"}, "qty": {"type": "integer"}},
                    "required": ["symbol", "qty"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["orders"],
        "additionalProperties": False,
    }
    bad = {"orders": [{"symbol": "AAPL", "qty": 1}, {"symbol": "MSFT", "qty": "two"}]}
    violations = validate(bad, schema, "arguments")
    assert any("orders[1].qty" == v.path for v in violations)


def test_describe_produces_an_mcp_tool_list():
    d = registry().describe()
    assert d[0]["name"] == "quote"
    assert d[0]["inputSchema"]["required"] == ["symbol", "quantity", "side"]
    assert "outputSchema" in d[0]


def test_failures_render_as_mcp_errors():
    r = registry().call("quote", {"symbol": "AAPL"})
    payload = to_mcp_result(r)
    assert payload["isError"] is True
    assert "required" in payload["content"][0]["text"]


def test_successes_render_as_mcp_content():
    r = registry().call("quote", {"symbol": "AAPL", "quantity": 2, "side": "sell"})
    payload = to_mcp_result(r)
    assert payload["isError"] is False
    assert "total_cents" in payload["content"][0]["text"]
