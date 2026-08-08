"""A small tool set, including two that break their own contracts on purpose."""
from __future__ import annotations

from datetime import date, timedelta

from .contracts import Registry, Tool

PRICES = {"AAPL": 21450, "MSFT": 41230, "NVDA": 12870, "TSLA": 24990}


def build_registry() -> Registry:
    r = Registry()

    r.register(Tool(
        name="quote_order",
        description="Price an order for a known symbol",
        parameters={
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "enum": sorted(PRICES)},
                "quantity": {"type": "integer", "minimum": 1, "maximum": 10000},
                "side": {"type": "string", "enum": ["buy", "sell"]},
            },
            "required": ["symbol", "quantity", "side"],
            "additionalProperties": False,
        },
        returns={
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "unit_cents": {"type": "integer", "minimum": 0},
                "total_cents": {"type": "integer", "minimum": 0},
                "side": {"type": "string", "enum": ["buy", "sell"]},
            },
            "required": ["symbol", "unit_cents", "total_cents", "side"],
            "additionalProperties": False,
        },
        handler=lambda symbol, quantity, side: {
            "symbol": symbol, "unit_cents": PRICES[symbol],
            "total_cents": PRICES[symbol] * quantity, "side": side,
        },
    ))

    r.register(Tool(
        name="settlement_date",
        description="T+1 settlement date, skipping weekends",
        parameters={
            "type": "object",
            "properties": {"traded_on": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"}},
            "required": ["traded_on"],
            "additionalProperties": False,
        },
        returns={
            "type": "object",
            "properties": {
                "settles_on": {"type": "string"},
                "business_days": {"type": "integer", "minimum": 1},
            },
            "required": ["settles_on", "business_days"],
            "additionalProperties": False,
        },
        handler=_settlement,
    ))

    # Deliberately broken: returns a string where its contract says integer.
    r.register(Tool(
        name="broken_returns_wrong_type",
        description="A tool whose implementation drifted from its declaration",
        parameters={
            "type": "object",
            "properties": {"symbol": {"type": "string", "enum": sorted(PRICES)}},
            "required": ["symbol"],
            "additionalProperties": False,
        },
        returns={
            "type": "object",
            "properties": {"symbol": {"type": "string"}, "price_cents": {"type": "integer"}},
            "required": ["symbol", "price_cents"],
            "additionalProperties": False,
        },
        handler=lambda symbol: {"symbol": symbol, "price_cents": f"${PRICES[symbol] / 100:.2f}"},
    ))

    # Deliberately broken: leaks a field its contract does not declare.
    r.register(Tool(
        name="broken_leaks_a_field",
        description="A tool that returns more than it promised",
        parameters={
            "type": "object",
            "properties": {"symbol": {"type": "string", "enum": sorted(PRICES)}},
            "required": ["symbol"],
            "additionalProperties": False,
        },
        returns={
            "type": "object",
            "properties": {"symbol": {"type": "string"}, "price_cents": {"type": "integer"}},
            "required": ["symbol", "price_cents"],
            "additionalProperties": False,
        },
        handler=lambda symbol: {
            "symbol": symbol, "price_cents": PRICES[symbol],
            "internal_desk": "prop-desk-3", "cost_basis_cents": 19900,
        },
    ))

    r.register(Tool(
        name="broken_raises",
        description="A tool whose upstream is down",
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
        returns={"type": "object", "properties": {"ok": {"type": "boolean"}},
                 "required": ["ok"], "additionalProperties": False},
        handler=_raises,
    ))

    return r


def _settlement(traded_on: str) -> dict:
    y, m, d = (int(x) for x in traded_on.split("-"))
    day = date(y, m, d)
    nxt = day + timedelta(days=1)
    days = 1
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
        days += 1
    return {"settles_on": nxt.isoformat(), "business_days": days}


def _raises() -> dict:
    raise ConnectionError("market data feed unreachable")
