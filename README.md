# Toolwright

An MCP tool registry that validates arguments on the way in **and the tool's own return
value on the way out**.

Live: **https://toolwright.levelbrook.com**

An MCP server hands a model a set of tools and then executes whatever it asks for. That is
a remote execution surface where the caller is a text generator, so the contract cannot be
advisory.

Most implementations validate arguments and stop. That covers the obvious half.

## The half that gets skipped

The return side. A tool that quietly returns a shape its own declaration does not permit
corrupts the model's next step, and the resulting failure surfaces somewhere else entirely
— in a step that looks unrelated, hours later, with the trail cold.

So the same declared schema is enforced in both directions, and a tool that breaks its own
contract fails **at the tool**, where the blame belongs:

```
POST /api/call  {"name": "broken_leaks_a_field", "arguments": {"symbol": "AAPL"}}

{"ok": false,
 "violations": [
   {"where": "result", "path": "internal_desk",     "message": "is not a declared property"},
   {"where": "result", "path": "cost_basis_cents",  "message": "is not a declared property"}]}
```

Note what that example is: a tool leaking two internal fields it never promised to return.
Inbound-only validation hands both straight to the model.

The registry ships three deliberately broken tools — one returning the wrong type, one
leaking undeclared fields, one raising — so the outbound check has something real to catch
on the live page.

## Three smaller rules

**Unknown arguments are refused, not dropped.** Silently discarding an argument the model
believed it was passing produces a call that does something other than what was asked, with
no error anywhere. `additionalProperties` also **defaults to closed**, so forgetting to
close a contract on a tool an LLM can call is safe.

**A boolean is not a number.** `bool` subclasses `int` in Python, so `{"quantity": true}`
slips through a naive `isinstance` check and reaches the handler as `1`.

**A tool that raises is a failed call, never a result.** With the exception text preserved
rather than flattened into a generic failure.

## Tests

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
.venv/bin/python -m pytest tests -q     # 24 tests
```

Both invariants were verified red before being kept. Skipping outbound validation and
letting unknown properties through turns five tests failing — including every case where a
tool violates its own declaration.

There is also a test asserting the handler does not run at all when arguments are invalid,
because "we validate arguments" is worth nothing if the call happens anyway.

## API

```
GET  /api/tools    the tool list an MCP client receives, with input and output schemas
POST /api/call     {"name": ..., "arguments": {...}} -> result plus its MCP rendering
GET  /up
```

## Layout

```
toolwright/contracts.py   schema validation, the registry, MCP rendering
toolwright/tools.py       sample tools, three broken on purpose
toolwright/main.py        FastAPI surface
```

## Limits

The validator implements a readable subset of JSON Schema — type, properties, required,
additionalProperties, enum, minimum, maximum, minLength, maxLength, pattern, items — chosen
because a contract nobody can read is a contract nobody checks. It has no `$ref`, no
`oneOf`/`anyOf`, no format assertions. This is also an HTTP surface shaped like MCP rather
than a stdio MCP server speaking the real JSON-RPC transport, and there is no auth, rate
limiting, or per-tool permission model — all of which matter as much as shape validation
once a model can reach real systems. Handlers are synchronous and in-process; timeouts and
cancellation for a tool that hangs are not modelled.

MIT.
