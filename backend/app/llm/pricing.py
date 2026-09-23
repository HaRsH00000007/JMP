"""Token pricing and cost estimation (docs/cost-model.md).

Rates are USD per million tokens (Anthropic first-party list prices at build time; verify against the
current pricing page before budgeting). Cache writes: 1.25× input (5-minute TTL) or 2× (1-hour TTL);
cache reads: 0.1× input (0.025× on Claude Fable 5.1). Message Batches: 50% of all token prices.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

PRICING_VERSION = "2026-09"


@dataclass(frozen=True)
class ModelRate:
    input: Decimal
    output: Decimal
    cache_read_multiplier: Decimal = Decimal("0.1")


RATES: dict[str, ModelRate] = {
    "claude-opus-5": ModelRate(Decimal("5"), Decimal("25")),
    "claude-opus-4-8": ModelRate(Decimal("5"), Decimal("25")),
    "claude-sonnet-5": ModelRate(Decimal("2"), Decimal("10")),
    "claude-sonnet-4-6": ModelRate(Decimal("3"), Decimal("15")),
    "claude-haiku-4-5": ModelRate(Decimal("1"), Decimal("5")),
    "claude-fable-5-1": ModelRate(Decimal("10"), Decimal("50"), Decimal("0.025")),
    "mock": ModelRate(Decimal("0"), Decimal("0")),
}
MILLION = Decimal(1_000_000)


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int = 0
    cache_creation_5m: int = 0
    cache_creation_1h: int = 0
    cache_read: int = 0
    output_tokens: int = 0

    @property
    def cache_creation(self) -> int:
        return self.cache_creation_5m + self.cache_creation_1h


def rate_for(model: str) -> ModelRate:
    if model in RATES:
        return RATES[model]
    for key, rate in RATES.items():  # tolerate suffixed ids, e.g. provider-prefixed
        if key in model:
            return rate
    raise KeyError(f"No pricing configured for model {model!r} — add it to app/llm/pricing.py")


def cost_usd(model: str, u: TokenUsage, *, batch: bool = False) -> Decimal:
    r = rate_for(model)
    total = (
        Decimal(u.input_tokens) * r.input
        + Decimal(u.cache_creation_5m) * r.input * Decimal("1.25")
        + Decimal(u.cache_creation_1h) * r.input * Decimal("2")
        + Decimal(u.cache_read) * r.input * r.cache_read_multiplier
        + Decimal(u.output_tokens) * r.output
    ) / MILLION
    if batch:
        total *= Decimal("0.5")
    return total.quantize(Decimal("0.000001"))


def usd_to_inr(usd: Decimal, rate: float) -> Decimal:
    return (usd * Decimal(str(rate))).quantize(Decimal("0.0001"))


def projection(model: str, *, prefix_tokens: int, variable_tokens: int, output_tokens: int, cache_ttl: str = "5m",
               batch: bool = False, usd_inr: float = 88.0, cache_hit_rate: float = 1.0) -> dict[str, object]:
    """Estimated cost per JMP / 100 / 1,000 for a given token profile.

    `cache_hit_rate` is the share of requests that read the static prefix from cache (the rest write it).
    """
    hit = TokenUsage(input_tokens=variable_tokens, cache_read=prefix_tokens, output_tokens=output_tokens)
    miss = TokenUsage(input_tokens=variable_tokens, output_tokens=output_tokens,
                      cache_creation_5m=prefix_tokens if cache_ttl == "5m" else 0,
                      cache_creation_1h=prefix_tokens if cache_ttl == "1h" else 0)
    per = (cost_usd(model, hit, batch=batch) * Decimal(str(cache_hit_rate))
           + cost_usd(model, miss, batch=batch) * Decimal(str(1 - cache_hit_rate)))
    per = per.quantize(Decimal("0.000001"))
    return {
        "model": model, "tier": "batch" if batch else "standard", "cache_ttl": cache_ttl,
        "cache_hit_rate": cache_hit_rate,
        "tokens": {"static_prefix": prefix_tokens, "variable_input": variable_tokens, "output": output_tokens},
        "usd": {"per_jmp": float(per), "per_100": float(per * 100), "per_1000": float(per * 1000)},
        "inr": {"per_jmp": float(usd_to_inr(per, usd_inr)), "per_100": float(usd_to_inr(per * 100, usd_inr)),
                "per_1000": float(usd_to_inr(per * 1000, usd_inr))},
        "usd_to_inr": usd_inr, "pricing_version": PRICING_VERSION,
    }
