"""What API calls cost, and the guard that stops a run before it spends too much.

This file only holds prices. Which model is called always comes from the environment
(ANTHROPIC_MODEL_CLASSIFY / ANTHROPIC_MODEL_EXTRACT). A model with no price here refuses to run,
because then the spend cap couldn't be enforced.

Prices: USD per million tokens, from https://platform.claude.com/docs/en/about-claude/pricing
(checked 2026-11-01). Cache writes are the 5-minute kind (the only one this app uses).
"""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Price:
    input: Decimal
    output: Decimal
    cache_write: Decimal
    cache_read: Decimal


@dataclass(frozen=True)
class ModelPrice:
    standard: Price
    # Some models (Haiku 5.5) charge more when the whole prompt (input + cache reads + cache
    # writes) is over this many tokens.
    long_prompt_tokens: int | None = None
    long: Price | None = None

    def for_prompt(self, prompt_tokens: int) -> Price:
        if self.long and self.long_prompt_tokens and prompt_tokens > self.long_prompt_tokens:
            return self.long
        return self.standard


def _p(input: str, output: str, cache_write: str, cache_read: str) -> Price:
    return Price(Decimal(input), Decimal(output), Decimal(cache_write), Decimal(cache_read))


PRICES: dict[str, ModelPrice] = {
    "claude-haiku-5-5": ModelPrice(
        _p("0.10", "0.50", "0.125", "0.01"),
        long_prompt_tokens=100_000,
        long=_p("0.50", "2.50", "0.625", "0.05"),
    ),
    "claude-sonnet-5-5": ModelPrice(_p("2", "10", "2.50", "0.10")),
    "claude-opus-5-5": ModelPrice(_p("4", "20", "5", "0.20")),
}

MILLION = Decimal(1_000_000)


class UnknownModelPrice(Exception):
    pass


def price_for(model: str) -> ModelPrice:
    if model in PRICES:
        return PRICES[model]
    # A dated snapshot name ("claude-x-1-20260101") is priced like its family name.
    for name, price in PRICES.items():
        if model.startswith(name + "-"):
            return price
    raise UnknownModelPrice(
        f"no price for model {model!r}; add it to app/extraction/pricing.py so the spend cap works"
    )


def cost_usd(
    model: str, input_tokens: int, output_tokens: int, cache_write: int = 0, cache_read: int = 0
) -> Decimal:
    price = price_for(model).for_prompt(input_tokens + cache_write + cache_read)
    total = (
        input_tokens * price.input
        + output_tokens * price.output
        + cache_write * price.cache_write
        + cache_read * price.cache_read
    )
    return (total / MILLION).quantize(Decimal("0.000001"))


def worst_case_usd(model: str, input_tokens: int, max_output_tokens: int) -> Decimal:
    """The most a call can cost: every input token uncached (or written to the cache, which costs
    more) and the full max_tokens of output."""
    price = price_for(model).for_prompt(input_tokens)
    total = input_tokens * max(price.input, price.cache_write) + max_output_tokens * price.output
    return (total / MILLION).quantize(Decimal("0.000001"))


class SpendLimitReached(Exception):
    pass


class SpendGuard:
    """Refuses a call when what's been spent plus that call's worst case would pass the limit.
    Because it plans for the worst case, a run can't go over the limit."""

    def __init__(self, limit_usd: Decimal | float | str, already_spent: Decimal = Decimal(0)):
        self.limit = Decimal(str(limit_usd))
        self.spent = already_spent

    def check(self, worst_case: Decimal) -> None:
        if self.spent + worst_case > self.limit:
            raise SpendLimitReached(
                f"spent ${self.spent} of ${self.limit}; the next call could cost up to "
                f"${worst_case}, so the run stops here"
            )

    def add(self, cost: Decimal) -> None:
        self.spent += cost
