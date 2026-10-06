"""Source-backed offers and explicit FX; no language model participates in valuation."""

import re
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Offer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ref_id: str = Field(min_length=1)
    kind: Literal["replacement", "used", "item"]
    country: str = Field(min_length=2)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    amount: float = Field(ge=0, allow_inf_nan=False)
    high: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    source: str = Field(min_length=2)
    url: str
    retrieved_at: date
    condition_assumed: str = Field(min_length=2)
    match_basis: str = Field(min_length=3)
    verified: Literal[True]
    # Values in a source currency may only be converted with a dated FX source.
    target_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    fx_rate: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    fx_url: str = ""
    fx_date: date | None = None

    @model_validator(mode="after")
    def evidence(self):
        for url in [self.url] + ([self.fx_url] if self.target_currency else []):
            parsed = urlparse(url)
            if (
                parsed.scheme not in {"https", "http"}
                or not parsed.hostname
                or parsed.username
            ):
                raise ValueError("A retrievable HTTP(S) source URL is required")
        if self.retrieved_at > date.today() or (
            self.fx_date and self.fx_date > date.today()
        ):
            raise ValueError("Retrieval dates cannot be in the future")
        if self.high is not None and self.high < self.amount:
            raise ValueError("Range high must be at least low")
        if self.target_currency and (not self.fx_rate or not self.fx_date):
            raise ValueError("Currency conversion requires a rate, source and date")
        return self


def money(value):
    return float(Decimal(str(value)).quantize(Decimal(".01"), rounding=ROUND_HALF_UP))


def needs_appraisal(line: dict, threshold: float) -> bool:
    traits = " ".join(
        str(line.get(k, ""))
        for k in ("edition", "description", "claimant_notes", "category")
    )
    if line.get("appraisal_required") or re.search(
        r"\b(signed|rare|antiquarian|first edition)\b", traits, re.I
    ):
        return True
    if re.search(
        r"\b(art|painting|portrait)\b", line.get("category", ""), re.I
    ) and not line.get("is_print"):
        return True
    return False


def apply_prices(
    packet: dict, *, country: str | None = None, currency: str | None = None
) -> dict:
    country, currency = (
        country or packet["sweep"]["country"],
        currency or packet["sweep"]["currency"],
    )
    threshold = packet.get("appraisal_threshold", 2000)
    offers = [Offer.model_validate(o) for o in packet.get("offers", [])]
    output = {"country": country, "currency": currency, "lines": [], "priced": 0}
    for line in packet["books"] + packet["items"]:
        is_book = "title" in line
        blocked = needs_appraisal(line, threshold)
        prices = {}
        for kind in (["replacement", "used"] if is_book else ["item"]):
            available = [
                o
                for o in offers
                if o.ref_id == line["id"]
                and o.kind == kind
                and (
                    (
                        o.country.casefold() == country.casefold()
                        and o.currency == currency
                        and not o.target_currency
                    )
                    or (o.target_currency == currency and o.fx_rate)
                )
            ]
            # Prefer local quotes; latest retrieval breaks ties. Never use an ebook as a physical equivalent.
            available.sort(
                key=lambda o: (
                    o.country.casefold() == country.casefold()
                    and not o.target_currency,
                    o.retrieved_at,
                ),
                reverse=True,
            )
            if not available or blocked or (is_book and not line.get("title")):
                continue
            offer = available[0]
            if not is_book and not line.get("brand_model"):
                if offer.high is None or offer.high == offer.amount:
                    comparable = [o for o in available if o.currency == offer.currency and o.target_currency == offer.target_currency and o.fx_rate == offer.fx_rate]
                    if len(comparable) < 2:
                        continue
                    offer = offer.model_copy(update={"amount": min(o.amount for o in comparable), "high": max(o.high if o.high is not None else o.amount for o in comparable)})
            rate = Decimal(str(offer.fx_rate)) if offer.target_currency else Decimal(1)
            amount, high = money(Decimal(str(offer.amount)) * rate), money(
                Decimal(str(offer.high if offer.high is not None else offer.amount))
                * rate
            )
            if high >= threshold:
                blocked = True
                prices.clear()
                break
            prices[kind] = {
                **offer.model_dump(mode="json"),
                "amount": amount,
                "low": amount,
                "high": high,
                "currency": currency,
                "converted": bool(offer.target_currency),
                "original_amount": offer.amount,
                "original_currency": offer.currency,
                "verification": "source checked by operator",
                "comparable_sources": [o.model_dump(mode="json") for o in available] if not is_book and not line.get("brand_model") else [],
            }
        output["lines"].append(
            {
                "ref_id": line["id"],
                "title": line.get("title", line.get("description")),
                "prices": prices,
                "needs_appraisal": blocked,
            }
        )
        output["priced"] += bool(prices)
        if (
            country == packet["sweep"]["country"]
            and currency == packet["sweep"]["currency"]
        ):
            if is_book:
                line["replacement_cost"] = prices.get(
                    "replacement",
                    {
                        "amount": None,
                        "source": "",
                        "url": "",
                        "retrieved_at": "",
                        "converted": False,
                        "condition_assumed": "",
                    },
                )
                line["used_value"] = prices.get(
                    "used",
                    {
                        "amount": None,
                        "source": "",
                        "url": "",
                        "retrieved_at": "",
                        "condition_assumed": "",
                    },
                )
                line["status"] = (
                    "needs_appraisal"
                    if blocked
                    else ("identified" if line.get("title") else "unidentified")
                )
            else:
                line["replacement_cost"] = prices.get(
                    "item",
                    {
                        "low": None,
                        "high": None,
                        "source": "",
                        "url": "",
                        "retrieved_at": "",
                    },
                )
                line["status"] = (
                    "needs_appraisal"
                    if not prices
                    else (
                        "range"
                        if not line.get("brand_model") or prices["item"]["low"] != prices["item"]["high"]
                        else "priced"
                    )
                )
    return output
