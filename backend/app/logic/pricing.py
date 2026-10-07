"""Price evidence models, matching, locale table and the deterministic valuation."""

from __future__ import annotations

import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from difflib import SequenceMatcher
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ..config import settings
from .utils import normalized


def rate_key(base: str, quote: str) -> str:
    return f"{base}->{quote}"


PriceKind = Literal["replacement", "used", "item"]
Verification = Literal["operator_checked", "provider_matched"]


def _retrievable(url: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.scheme in {"https", "http"}
        and bool(parsed.hostname)
        and not parsed.username
    )


class Quote(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ref_id: str = Field(min_length=1)
    kind: PriceKind
    amount: float = Field(ge=0, allow_inf_nan=False)
    high: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    country: str = Field(min_length=2)
    country_code: str = Field(default="", pattern=r"^([A-Z]{2})?$")
    source: str = Field(min_length=2)
    url: str
    retrieved_at: date
    condition_assumed: str = Field(min_length=2)
    match_basis: str = Field(min_length=3)
    match_score: float = Field(default=1.0, ge=0, le=1)
    is_ebook: bool = False
    verification: Verification
    provider: str = ""
    listing_title: str = ""
    comparables: list[dict] = Field(default_factory=list)
    # Conversion evidence supplied with the quote itself (operator-entered foreign offers).
    target_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    fx_rate: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    fx_url: str = ""
    fx_date: date | None = None
    fx_source: str = ""

    @model_validator(mode="after")
    def evidence(self) -> Quote:
        urls = [self.url] + ([self.fx_url] if self.target_currency else [])
        if any(not _retrievable(url) for url in urls):
            raise ValueError("A retrievable HTTP(S) source URL is required")
        today = date.today()
        if self.retrieved_at > today or (self.fx_date and self.fx_date > today):
            raise ValueError("Retrieval dates cannot be in the future")
        if self.high is not None and self.high < self.amount:
            raise ValueError("Range high must be at least low")
        if self.target_currency and (not self.fx_rate or not self.fx_date):
            raise ValueError("Currency conversion requires a rate, source and date")
        return self


class Offer(BaseModel):
    """Operator-entered price evidence (review form body). Strict: unknown fields fail."""

    model_config = ConfigDict(extra="forbid")

    ref_id: str = Field(min_length=1)
    kind: PriceKind
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
    target_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    fx_rate: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    fx_url: str = ""
    fx_date: date | None = None

    @model_validator(mode="after")
    def evidence(self) -> Offer:
        Quote.model_validate(self.to_quote())
        return self

    def to_quote(self) -> dict:
        return {
            **self.model_dump(mode="json", exclude={"verified"}),
            "verification": "operator_checked",
            "provider": "operator",
            "match_score": 1.0,
        }


DEFAULT_APPRAISAL_THRESHOLD = 2000.0
SPECIAL_EDITION = re.compile(
    r"\b(signed|rare|antiquarian|first edition|limited edition|inscribed)\b", re.I
)
ARTWORK = re.compile(r"\b(art|painting|portrait|sculpture|print)\b", re.I)


def is_artwork(line: dict) -> bool:
    return bool(ARTWORK.search(str(line.get("category", ""))))


def needs_appraisal(line: dict, threshold: float = DEFAULT_APPRAISAL_THRESHOLD) -> bool:
    """True when the line must not be auto-priced.

    Special editions, signed or antiquarian copies, and original artwork are never
    priced from market listings. Artwork defaults to appraisal unless the claimant
    stated it is a print. The value threshold itself is applied in valuation once an
    amount is known.
    """
    traits = " ".join(
        str(line.get(key, "")) for key in ("edition", "description", "claimant_notes")
    )
    if line.get("appraisal_required") or SPECIAL_EDITION.search(traits):
        return True
    if is_artwork(line) and line.get("is_print") is not True:
        return True
    return False


def appraisal_reason(line: dict, threshold: float) -> str:
    if line.get("appraisal_required"):
        return (
            "Claimant stated a special edition or original; human appraisal required."
        )
    traits = " ".join(
        str(line.get(key, "")) for key in ("edition", "description", "claimant_notes")
    )
    if SPECIAL_EDITION.search(traits):
        return "Signed, rare, antiquarian or first edition: human appraisal required."
    if is_artwork(line) and line.get("is_print") is not True:
        return "Artwork is treated as an original until the claimant confirms it is a print."
    return f"Value at or above the appraisal threshold ({threshold:g}); human appraisal required."


NOISE = re.compile(
    r"\b(paperback|hardcover|hardback|edition|new|used|book|novel|by|the|a|an|of|and)\b"
)


def title_similarity(expected: str, listing: str) -> float:
    """Similarity in [0, 1]; a listing containing the whole title scores at least 0.9."""
    expected_norm, listing_norm = normalized(expected), normalized(listing)
    if not expected_norm or not listing_norm:
        return 0.0
    if expected_norm in listing_norm:
        return max(0.9, SequenceMatcher(None, expected_norm, listing_norm).ratio())
    cleaned = normalized(NOISE.sub(" ", listing_norm))
    return max(
        SequenceMatcher(None, expected_norm, listing_norm).ratio(),
        SequenceMatcher(None, expected_norm, cleaned).ratio(),
    )


def author_present(author: str, listing: str) -> bool | None:
    """None when no author is known; otherwise whether the surname appears."""
    words = normalized(author).split()
    if not words:
        return None
    return words[-1] in normalized(listing).split()


def match_score(
    book: dict, listing_title: str, *, isbn_match: bool = False
) -> tuple[float, str]:
    """Score a listing against the identified book and explain the basis."""
    if isbn_match:
        return 1.0, "ISBN read from the spine matches the listing"
    score = title_similarity(book.get("title", ""), listing_title)
    author = author_present(book.get("author", ""), listing_title)
    if author is False:
        score *= 0.8
        basis = "Title similarity; author surname not found in the listing title"
    elif author is True:
        basis = "Title similarity with the author surname present in the listing title"
    else:
        basis = "Title similarity; no author read from the spine"
    return round(min(score, 1.0), 3), basis


# ISO 3166-1 alpha-2 -> display name, ISO 4217 currency, eBay marketplace serving that country.
# eBay has no marketplace for several countries; EBAY_US with a deliveryCountry filter is
# then the closest retrievable market and its USD quotes are converted with dated FX.
_LOCALES: dict[str, tuple[str, str, str]] = {
    "AE": ("United Arab Emirates", "AED", "EBAY_US"),
    "AU": ("Australia", "AUD", "EBAY_AU"),
    "AT": ("Austria", "EUR", "EBAY_AT"),
    "BE": ("Belgium", "EUR", "EBAY_BE"),
    "CA": ("Canada", "CAD", "EBAY_CA"),
    "CH": ("Switzerland", "CHF", "EBAY_CH"),
    "DE": ("Germany", "EUR", "EBAY_DE"),
    "ES": ("Spain", "EUR", "EBAY_ES"),
    "FR": ("France", "EUR", "EBAY_FR"),
    "GB": ("United Kingdom", "GBP", "EBAY_GB"),
    "HK": ("Hong Kong", "HKD", "EBAY_HK"),
    "IE": ("Ireland", "EUR", "EBAY_IE"),
    "IN": ("India", "INR", "EBAY_IN"),
    "IT": ("Italy", "EUR", "EBAY_IT"),
    "MY": ("Malaysia", "MYR", "EBAY_MY"),
    "NL": ("Netherlands", "EUR", "EBAY_NL"),
    "NZ": ("New Zealand", "NZD", "EBAY_AU"),
    "PH": ("Philippines", "PHP", "EBAY_PH"),
    "PL": ("Poland", "PLN", "EBAY_PL"),
    "SA": ("Saudi Arabia", "SAR", "EBAY_US"),
    "SG": ("Singapore", "SGD", "EBAY_SG"),
    "US": ("United States", "USD", "EBAY_US"),
    "ZA": ("South Africa", "ZAR", "EBAY_US"),
}
_BY_NAME = {name.casefold(): code for code, (name, _, _) in _LOCALES.items()}

# schema for country code and currency
class Locale(BaseModel):
    country: str = Field(min_length=2)
    currency: str = Field(pattern=r"^[A-Z]{3}$") #validate country code must be capital letters of three words
    country_code: str = Field(default="", pattern=r"^([A-Z]{2})?$") # either empty or two capital letters
    ebay_marketplace: str = ""   # empty and fills from table

    def matches(self, other_country: str, other_currency: str) -> bool:
        return (
            other_country.casefold() == self.country.casefold()
            and other_currency.upper() == self.currency
        )


def known_locales() -> list[dict]:
    """Supported countries with their default currency, for the setup screen."""
    return [
        {"country_code": code, "country": name, "currency": currency}
        for code, (name, currency, _) in sorted(
            _LOCALES.items(), key=lambda kv: kv[1][0]
        )
    ]


def resolve_locale(
    *,
    country_code: str = "",
    country: str = "",
    currency: str = "",
    marketplace: str = "",
) -> Locale:
    """Fill missing locale fields from the table; explicit values always win."""
    code = country_code.upper().strip()
    if not code and country:
        code = _BY_NAME.get(country.casefold().strip(), "")
    name, default_currency, default_marketplace = _LOCALES.get(
        code, ("", "", "EBAY_US")
    )
    resolved_country = (
        name or country.strip()
    )  # table spelling wins when the country is known
    resolved_currency = currency.upper().strip() or default_currency
    if not resolved_country or not resolved_currency:
        raise ValueError(
            "Country and currency are required; supply a known country code"
        )
    return Locale(
        country=resolved_country,
        currency=resolved_currency,
        country_code=code,
        ebay_marketplace=marketplace or default_marketplace,
    )


def sweep_locale(packet: dict, marketplace: str = "") -> Locale:
    sweep = packet["sweep"]
    return resolve_locale(
        country_code=sweep.get("country_code", ""),
        country=sweep.get("country", ""),
        currency=sweep.get("currency", ""),
        marketplace=marketplace,
    )


EMPTY_REPLACEMENT = {
    "amount": None,
    "source": "",
    "url": "",
    "retrieved_at": "",
    "converted": False,
}
EMPTY_USED = {
    "amount": None,
    "source": "",
    "url": "",
    "retrieved_at": "",
    "condition_assumed": "",
}
EMPTY_ITEM_PRICE = {
    "low": None,
    "high": None,
    "source": "",
    "url": "",
    "retrieved_at": "",
}


def money(value) -> float:
    return float(Decimal(str(value)).quantize(Decimal(".01"), rounding=ROUND_HALF_UP))


def _is_book(line: dict) -> bool:
    return "title" in line


def _all_quotes(packet: dict) -> list[Quote]:
    quotes: list[Quote] = []
    for offer in packet.get("offers", []):
        quotes.append(Quote.model_validate(Offer.model_validate(offer).to_quote()))
    for raw in packet.get("quotes", []):
        try:
            quotes.append(Quote.model_validate(raw))
        except ValidationError:
            continue
    return quotes


def _summary(quote: Quote) -> dict:
    return {
        "provider": quote.provider,
        "verification": quote.verification,
        "amount": quote.amount,
        "high": quote.high,
        "currency": quote.currency,
        "country": quote.country,
        "source": quote.source,
        "url": quote.url,
        "retrieved_at": quote.retrieved_at.isoformat(),
        "condition_assumed": quote.condition_assumed,
        "match_score": quote.match_score,
        "listing_title": quote.listing_title,
        "is_ebook": quote.is_ebook,
    }


def _conversion(quote: Quote, currency: str, fx_rates: dict) -> tuple[dict | None, str]:
    """Return FX evidence for the quote, or a rejection reason."""
    if quote.currency == currency and not quote.target_currency:
        return None, ""
    if quote.target_currency == currency and quote.fx_rate:
        return {
            "rate": quote.fx_rate,
            "fx_url": quote.fx_url,
            "fx_date": quote.fx_date.isoformat() if quote.fx_date else "",
            "fx_source": quote.fx_source or "Operator-supplied dated rate",
        }, ""
    recorded = fx_rates.get(rate_key(quote.currency, currency))
    if recorded:
        return recorded, ""
    return None, f"No dated FX evidence to convert {quote.currency} into {currency}"


def _accept(
    quotes: list[Quote],
    line: dict,
    kind: str,
    country: str,
    currency: str,
    fx_rates: dict,
) -> tuple[list[tuple[Quote, dict | None]], list[dict]]:
    minimum = settings.pricing_min_match
    accepted, rejected = [], []
    for quote in quotes:
        if quote.ref_id != line["id"] or quote.kind != kind:
            continue
        reason = ""
        if quote.verification == "provider_matched" and quote.match_score < minimum:
            reason = f"Listing does not match the identified work (score {quote.match_score:.2f} < {minimum:.2f})"
        elif (
            quote.is_ebook
            and kind == "replacement"
            and not settings.pricing_allow_ebook_proxy
        ):
            reason = "Ebook list price is not a physical replacement cost"
        else:
            fx, reason = _conversion(quote, currency, fx_rates)
        if reason:
            rejected.append({**_summary(quote), "reason": reason})
            continue
        accepted.append((quote, fx))
    local = (
        lambda q: q.country.casefold() == country.casefold() and q.currency == currency
    )  # noqa: E731
    accepted.sort(
        key=lambda pair: (
            pair[0].verification == "operator_checked",
            local(pair[0]),
            pair[0].match_score,
            pair[0].retrieved_at,
        ),
        reverse=True,
    )
    return accepted, rejected


def _priced(
    quote: Quote, fx: dict | None, currency: str, low: float, high: float
) -> dict:
    rate = Decimal(str(fx["rate"])) if fx else Decimal(1)
    value = {
        **quote.model_dump(
            mode="json",
            exclude={"target_currency", "fx_rate", "fx_url", "fx_date", "fx_source"},
        ),
        "amount": money(Decimal(str(low)) * rate),
        "low": money(Decimal(str(low)) * rate),
        "high": money(Decimal(str(high)) * rate),
        "currency": currency,
        "converted": bool(fx),
        "original_amount": low,
        "original_currency": quote.currency,
    }
    if fx:
        value.update(
            fx_rate=fx["rate"],
            fx_url=fx.get("fx_url", ""),
            fx_date=fx.get("fx_date", ""),
            fx_source=fx.get("fx_source", ""),
        )
    return value


def _select(
    accepted: list[tuple[Quote, dict | None]], line: dict, kind: str, currency: str
) -> tuple[dict | None, str]:
    if not accepted:
        return None, "no_source"
    quote, fx = accepted[0]
    if kind != "item" or line.get("brand_model"):
        return (
            _priced(
                quote,
                fx,
                currency,
                quote.amount,
                quote.high if quote.high is not None else quote.amount,
            ),
            "priced",
        )
    # Unknown brand/model: a sourced range is required — an explicit range, recorded
    # comparables, or at least two comparable quotes in the same currency basis.
    if quote.high is not None and quote.high != quote.amount:
        return _priced(quote, fx, currency, quote.amount, quote.high), "range"
    if len(quote.comparables) >= 2:
        amounts = [
            c["amount"]
            for c in quote.comparables
            if c.get("currency") == quote.currency
        ]
        if len(amounts) >= 2:
            return _priced(quote, fx, currency, min(amounts), max(amounts)), "range"
    same_basis = [
        q
        for q, f in accepted
        if q.currency == quote.currency
        and (f or {}).get("rate") == (fx or {}).get("rate")
    ]
    if len(same_basis) >= 2:
        amounts = [q.amount for q in same_basis] + [
            q.high for q in same_basis if q.high is not None
        ]
        value = _priced(quote, fx, currency, min(amounts), max(amounts))
        value["comparable_sources"] = [_summary(q) for q in same_basis]
        return value, "range"
    return None, "needs_range"


def apply_prices(
    packet: dict,
    *,
    country: str | None = None,
    currency: str | None = None,
    write: bool | None = None,
) -> dict:
    """Value every line for `country`/`currency`; write to the lines for the claim locale."""
    sweep = packet["sweep"]
    country, currency = country or sweep["country"], currency or sweep["currency"]
    if write is None:
        write = country == sweep["country"] and currency == sweep["currency"]
    threshold = float(packet.get("appraisal_threshold", DEFAULT_APPRAISAL_THRESHOLD))
    quotes, fx_rates = _all_quotes(packet), packet.get("fx_rates", {})
    output = {
        "country": country,
        "currency": currency,
        "lines": [],
        "priced": 0,
        "details": {},
    }

    for line in packet["books"] + packet["items"]:
        is_book = _is_book(line)
        kinds = ("replacement", "used") if is_book else ("item",)
        detail = {
            "ref_id": line["id"],
            "kind": "book" if is_book else "item",
            "country": country,
            "currency": currency,
        }
        blocked = needs_appraisal(line, threshold)
        selected: dict[str, dict] = {}
        for kind in kinds:
            accepted, rejected = _accept(
                quotes, line, kind, country, currency, fx_rates
            )
            entry = {
                "candidates": [_summary(q) for q, _ in accepted],
                "rejected": rejected,
                "selected": None,
                "status": (
                    "not_identified"
                    if is_book and not line.get("title")
                    else "no_source"
                ),
            }
            if blocked:
                entry["status"] = "needs_appraisal"
            elif not (is_book and not line.get("title")):
                value, status = _select(accepted, line, kind, currency)
                entry["status"] = status
                if value is not None:
                    if value["high"] >= threshold:
                        blocked = True
                        entry["status"] = "needs_appraisal"
                    else:
                        entry["selected"] = value
                        selected[kind] = value
            detail[kind] = entry
        if blocked:
            detail["appraisal_reason"] = appraisal_reason(line, threshold)
            for kind in kinds:
                detail[kind].update(selected=None, status="needs_appraisal")
            selected.clear()
        output["details"][line["id"]] = detail
        output["lines"].append(
            {
                "ref_id": line["id"],
                "title": line.get("title", line.get("description")),
                "prices": selected,
                "needs_appraisal": blocked,
            }
        )
        output["priced"] += bool(selected)
        if write:
            _write_line(line, is_book, selected, blocked)
    if write:
        packet["price_details"] = output["details"]
    return output


def _write_line(
    line: dict, is_book: bool, selected: dict[str, dict], blocked: bool
) -> None:
    if is_book:
        line["replacement_cost"] = selected.get("replacement", dict(EMPTY_REPLACEMENT))
        line["used_value"] = selected.get("used", dict(EMPTY_USED))
        if blocked:
            line["status"] = "needs_appraisal"
        else:
            line["status"] = "identified" if line.get("title") else "unidentified"
        return
    price = selected.get("item")
    line["replacement_cost"] = price or dict(EMPTY_ITEM_PRICE)
    if blocked or price is None:
        line["status"] = "needs_appraisal"
    elif not line.get("brand_model") or price["low"] != price["high"]:
        line["status"] = "range"
    else:
        line["status"] = "priced"
