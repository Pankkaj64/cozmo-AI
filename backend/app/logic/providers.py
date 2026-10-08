"""Price and FX retrieval (eBay, Google Books, ECB/ER-API), research and second-country comparison."""

from __future__ import annotations

import asyncio
import base64
import copy
import statistics
import time
from datetime import UTC, datetime
from typing import Protocol

import httpx

from ..config import (
    EBAY_OAUTH_SCOPE,
    FRANKFURTER_URL,
    GOOGLE_BOOKS_VOLUMES_URL,
    OPEN_ER_API_URL,
    settings,
)
from .identification import resolve_isbn, resolve_work
from .pricing import (
    Locale,
    apply_prices,
    match_score,
    needs_appraisal,
    normalized,
    rate_key,
    sweep_locale,
)
from .utils import stamp


class ProviderResult(dict):
    """`{"status": "candidates|not_found|not_configured|unavailable", "quotes": [...]}`."""


class PriceProvider(Protocol):
    name: str

    async def get_book_quotes(
        self, book: dict, locale: Locale, client: httpx.AsyncClient
    ) -> ProviderResult: ...

    async def get_item_quotes(
        self, item: dict, locale: Locale, client: httpx.AsyncClient
    ) -> ProviderResult: ...


def configured_providers() -> list[PriceProvider]:
    """Providers enabled by configuration, in the order their quotes are preferred."""

    providers: list[PriceProvider] = []
    if settings.ebay_configured:
        providers.append(EbayProvider())
    if settings.enable_google_books:
        providers.append(GoogleBooksProvider())
    return providers


def error_detail(exc: Exception) -> str:
    """Short, actionable provider error: the HTTP status says more than the exception class."""
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        hint = " (shared keyless quota exhausted; set GOOGLE_BOOKS_API_KEY)" if code == 429 else ""
        return f"HTTP {code}{hint}"
    return type(exc).__name__


def result(status: str, quotes: list[dict] | None = None, **fields) -> ProviderResult:
    return ProviderResult(status=status, quotes=quotes or [], **fields)


SOURCE = "Google Books list price"


class GoogleBooksProvider:
    name = "google_books"

    async def get_book_quotes(
        self, book: dict, locale: Locale, client: httpx.AsyncClient
    ) -> ProviderResult:
        if not locale.country_code:
            return result("needs_country_code", reason="Google Books needs a two-letter country")
        if book.get("isbn"):
            query = f"isbn:{book['isbn']}"
        else:
            # Plain text: the intitle:/inauthor: (and isbn:) operators return no volumes any
            # more (checked 8 Oct 2026). match_score() still rejects listings that do not fit.
            query = " ".join(filter(None, [book.get("title", ""), book.get("author", "")]))
        params = {
            "q": query,
            "country": locale.country_code,
            "maxResults": 20,  # most volumes are ebooks or not for sale; look further for print
            "printType": "books",
        }
        # The key travels in a header, so it never appears in URLs, logs or error messages.
        headers = (
            {"x-goog-api-key": settings.google_books_api_key}
            if settings.google_books_api_key
            else {}
        )
        for attempt in range(3):  # Google answers 503 intermittently; retry briefly
            response = await client.get(GOOGLE_BOOKS_VOLUMES_URL, params=params, headers=headers)
            if response.status_code != 503 or attempt == 2:
                break
            await asyncio.sleep(1 + attempt)
        response.raise_for_status()
        retrieved = datetime.now(UTC).date().isoformat()
        quotes, catalogue = [], []
        for item in response.json().get("items", []):
            info, sale = item.get("volumeInfo", {}), item.get("saleInfo", {})
            listing = " ".join([info.get("title", ""), " ".join(info.get("authors", []))])
            isbn13 = next(
                (
                    i.get("identifier")
                    for i in info.get("industryIdentifiers", [])
                    if i.get("type") == "ISBN_13"
                ),
                "",
            )
            isbn_match = bool(book.get("isbn")) and normalized(book["isbn"]) == normalized(isbn13)
            score, basis = match_score(book, listing, isbn_match=isbn_match)
            catalogue.append(
                {
                    "title": info.get("title", ""),
                    "authors": info.get("authors", []),
                    "publisher": info.get("publisher", ""),
                    "published": info.get("publishedDate", ""),
                    "isbn13": isbn13,
                    "url": info.get("infoLink", ""),
                    "match_score": score,
                }
            )
            price = sale.get("listPrice") or sale.get("retailPrice") or {}
            url = sale.get("buyLink") or info.get("infoLink", "")
            if sale.get("saleability") != "FOR_SALE" or not price.get("amount") or not url:
                continue
            quotes.append(
                {
                    "ref_id": book["id"],
                    "kind": "replacement",
                    "amount": float(price["amount"]),
                    "currency": str(price.get("currencyCode", "")).upper(),
                    "country": locale.country,
                    "country_code": locale.country_code,
                    "source": SOURCE + (" (Google Play ebook)" if sale.get("isEbook") else ""),
                    "url": url,
                    "retrieved_at": retrieved,
                    "condition_assumed": "New ebook" if sale.get("isEbook") else "New",
                    "match_basis": basis,
                    "match_score": score,
                    "is_ebook": bool(sale.get("isEbook")),
                    "verification": "provider_matched",
                    "provider": self.name,
                    "listing_title": listing.strip(),
                }
            )
        return result(
            "candidates" if quotes else "not_found",
            quotes,
            catalogue=catalogue,
            calls=1,
            requested_country=locale.country_code,
        )

    async def get_item_quotes(
        self, item: dict, locale: Locale, client: httpx.AsyncClient
    ) -> ProviderResult:
        return result("not_applicable", reason="Google Books prices books only")


NEW_CONDITIONS = "{1000}"
USED_CONDITIONS = "{2750|3000|4000|5000|6000}"


def _base_url() -> str:
    return settings.ebay_api_url


class EbayAuth:
    """Application token cache for the client-credentials grant."""

    def __init__(self) -> None:
        self._token = ""
        self._expires = 0.0

    async def token(self, client: httpx.AsyncClient) -> str:
        if settings.ebay_access_token:
            return settings.ebay_access_token
        if self._token and time.monotonic() < self._expires - 60:
            return self._token
        credentials = base64.b64encode(
            f"{settings.ebay_client_id}:{settings.ebay_client_secret}".encode()
        ).decode()
        response = await client.post(
            f"{_base_url()}/identity/v1/oauth2/token",
            data={"grant_type": "client_credentials", "scope": EBAY_OAUTH_SCOPE},
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        response.raise_for_status()
        payload = response.json()
        self._token = payload["access_token"]
        self._expires = time.monotonic() + float(payload.get("expires_in", 7200))
        print(
            f"[DEBUG {stamp()}] ebay.token.refreshed expires_in_s={payload.get('expires_in')}",
            flush=True,
        )
        return self._token


_AUTH = EbayAuth()


class EbayProvider:
    name = "ebay"

    def __init__(self, auth: EbayAuth | None = None) -> None:
        self.auth = auth or _AUTH

    async def _search(
        self,
        query: str,
        locale: Locale,
        conditions: str | None,
        client: httpx.AsyncClient,
    ) -> list[dict]:
        if not locale.country_code:
            raise ValueError("eBay search needs a two-letter delivery country")
        filters = f"deliveryCountry:{locale.country_code},buyingOptions:{{FIXED_PRICE}}"
        if conditions:
            filters += f",conditionIds:{conditions}"
        marketplace = settings.ebay_marketplace_id or locale.ebay_marketplace
        response = await client.get(
            f"{_base_url()}/buy/browse/v1/item_summary/search",
            params={"q": query[:150], "limit": 10, "filter": filters},
            headers={
                "Authorization": f"Bearer {await self.auth.token(client)}",
                "X-EBAY-C-MARKETPLACE-ID": marketplace,
            },
        )
        response.raise_for_status()
        return response.json().get("itemSummaries", [])

    def _quotes(self, line: dict, kind: str, listings: list[dict], locale: Locale) -> list[dict]:
        retrieved = datetime.now(UTC).date().isoformat()
        quotes = []
        for item in listings:
            price = item.get("price", {})
            if not price.get("value") or not item.get("itemWebUrl"):
                continue
            listing_title = item.get("title", "")
            if kind == "item":
                score, basis = (
                    1.0,
                    "Category and brand/model keyword search; comparable listing",
                )
            else:
                score, basis = match_score(line, listing_title)
            quotes.append(
                {
                    "ref_id": line["id"],
                    "kind": kind,
                    "amount": float(price["value"]),
                    "currency": str(price.get("currency", "")).upper(),
                    "country": locale.country,
                    "country_code": locale.country_code,
                    "source": "eBay Browse API fixed-price listing (asking price, excl. shipping/tax)",
                    "url": item["itemWebUrl"],
                    "retrieved_at": retrieved,
                    "condition_assumed": item.get("condition", "Unspecified"),
                    "match_basis": basis,
                    "match_score": score,
                    "verification": "provider_matched",
                    "provider": self.name,
                    "listing_title": listing_title,
                    "seller_country": item.get("itemLocation", {}).get("country", ""),
                }
            )
        return quotes

    @staticmethod
    def median_quote(quotes: list[dict]) -> dict | None:
        """One representative quote: the median listing, carrying all comparables."""
        if not quotes:
            return None
        ordered = sorted(quotes, key=lambda q: q["amount"])
        median = statistics.median_low([q["amount"] for q in ordered])
        chosen = dict(next(q for q in ordered if q["amount"] == median))
        chosen["comparables"] = [
            {
                "url": q["url"],
                "amount": q["amount"],
                "currency": q["currency"],
                "condition": q["condition_assumed"],
                "listing_title": q["listing_title"],
            }
            for q in ordered
        ]
        chosen["source"] += f"; median of {len(ordered)} matching listings"
        if chosen["kind"] == "item":
            chosen["amount"], chosen["high"] = (
                ordered[0]["amount"],
                ordered[-1]["amount"],
            )
        return chosen

    async def get_book_quotes(
        self, book: dict, locale: Locale, client: httpx.AsyncClient
    ) -> ProviderResult:
        query = book.get("isbn") or " ".join(filter(None, [book.get("title"), book.get("author")]))
        quotes, attempts, calls = [], {}, 0
        minimum = settings.pricing_min_match
        for kind, conditions in (
            ("replacement", NEW_CONDITIONS),
            ("used", USED_CONDITIONS),
        ):
            try:
                calls += 1
                listings = await self._search(query, locale, conditions, client)
                candidates = [
                    q
                    for q in self._quotes(book, kind, listings, locale)
                    if q["match_score"] >= minimum
                ]
                attempts[kind] = {
                    "listings": len(listings),
                    "matching": len(candidates),
                }
                chosen = self.median_quote(candidates)
                if chosen:
                    quotes.append(chosen)
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                attempts[kind] = {"status": "unavailable", "error": error_detail(exc)}
                print(
                    f"[WARN {stamp()}] ebay.search.unavailable kind={kind} error_type={type(exc).__name__}",
                    flush=True,
                )
        return result(
            "candidates" if quotes else "not_found",
            quotes,
            attempts=attempts,
            calls=calls,
        )

    async def get_item_quotes(
        self, item: dict, locale: Locale, client: httpx.AsyncClient
    ) -> ProviderResult:
        query = " ".join(
            filter(
                None,
                [
                    item.get("brand_model"),
                    item.get("category"),
                    item.get("description"),
                ],
            )
        )
        if not query.strip():
            return result("not_found", reason="No category or brand/model to search")
        try:
            listings = await self._search(query, locale, NEW_CONDITIONS, client)
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            return result("unavailable", error=type(exc).__name__, calls=1)
        chosen = self.median_quote(self._quotes(item, "item", listings, locale))
        return result("candidates" if chosen else "not_found", [chosen] if chosen else [], calls=1)


async def fetch_rate(base: str, quote: str, client: httpx.AsyncClient) -> dict | None:
    """Return FX evidence for one currency pair, or None when no source answered."""
    base, quote = base.upper(), quote.upper()
    if base == quote:
        return {
            "base": base,
            "quote": quote,
            "rate": 1.0,
            "fx_url": "",
            "fx_date": datetime.now(UTC).date().isoformat(),
            "fx_source": "Same currency; no conversion",
            "retrieved_at": datetime.now(UTC).isoformat(),
        }
    retrieved = datetime.now(UTC).isoformat()
    try:
        response = await client.get(FRANKFURTER_URL, params={"from": base, "to": quote})
        if response.status_code == 200:
            payload = response.json()
            rate = payload.get("rates", {}).get(quote)
            if rate:
                return {
                    "base": base,
                    "quote": quote,
                    "rate": float(rate),
                    "fx_url": str(response.url),
                    "fx_date": payload.get("date", ""),
                    "fx_source": "Frankfurter (European Central Bank reference rates)",
                    "retrieved_at": retrieved,
                }
    except (httpx.HTTPError, ValueError) as exc:
        print(
            f"[WARN {stamp()}] fx.frankfurter.unavailable error_type={type(exc).__name__}",
            flush=True,
        )
    try:
        response = await client.get(f"{OPEN_ER_API_URL}/{base}")
        response.raise_for_status()
        payload = response.json()
        rate = payload.get("rates", {}).get(quote)
        if rate:
            return {
                "base": base,
                "quote": quote,
                "rate": float(rate),
                "fx_url": str(response.url),
                "fx_date": str(payload.get("time_last_update_utc", ""))[:16],
                "fx_source": "ExchangeRate-API open endpoint",
                "retrieved_at": retrieved,
            }
    except (httpx.HTTPError, ValueError) as exc:
        print(
            f"[WARN {stamp()}] fx.open_er_api.unavailable error_type={type(exc).__name__}",
            flush=True,
        )
    return None


async def ensure_rates(
    packet: dict, currencies: set[str], target: str, client: httpx.AsyncClient
) -> int:
    """Record evidence for every `currency -> target` pair missing from the packet."""
    rates = packet.setdefault("fx_rates", {})
    calls = 0
    for currency in sorted(currencies):
        if not currency or currency == target or rate_key(currency, target) in rates:
            continue
        calls += 1
        evidence = await fetch_rate(currency, target, client)
        if evidence:
            rates[rate_key(currency, target)] = evidence
    return calls


_PROVIDER_TIMEOUT_S = 10


def _priceable(line: dict, threshold: float) -> bool:
    if needs_appraisal(line, threshold):
        return False
    if "title" in line and not line.get("title") and not line.get("isbn"):
        return False
    return True


def _record_cost(packet: dict, provider: str, calls: int, elapsed_s: float) -> None:
    cost = packet.setdefault("cost", {})
    usage = cost.setdefault("provider_calls", {})
    entry = usage.setdefault(provider, {"calls": 0, "elapsed_s": 0.0})
    entry["calls"] += calls
    entry["elapsed_s"] = round(entry["elapsed_s"] + elapsed_s, 3)


def demote_author_title(line: dict, identification: dict) -> None:
    """The accepted title was a writer's name: keep it as the author, drop the identity."""
    line.update(
        proposed_title=line.get("title", ""),
        title="",
        author=line.get("author") or line.get("title", ""),
        status="unidentified",
        title_rejected=(
            f"'{line.get('title', '')}' is an author with {identification.get('work_count')} "
            "works in Open Library, so it cannot be this book's title."
        ),
    )


async def _catalogue(line: dict, client: httpx.AsyncClient) -> dict:
    if line.get("isbn"):
        metadata = await resolve_isbn(line["isbn"], client)
        if metadata and metadata["title"]:
            line.update({k: metadata[k] for k in ("title", "author", "publisher")})
            line.update(identity_source=metadata, status="identified")
        return metadata or {"status": "not_found"}
    return await resolve_work(line, client)


async def research_inventory(
    packet: dict,
    providers: list[PriceProvider] | None = None,
    *,
    locale: Locale | None = None,
    lines: list[dict] | None = None,
) -> list[dict]:
    """Collect quotes for every priceable line in `locale` (default: the sweep locale)."""
    locale = locale or sweep_locale(packet)
    providers = configured_providers() if providers is None else providers
    threshold = float(packet.get("appraisal_threshold", 2000))
    targets = lines if lines is not None else packet["books"] + packet["items"]
    results: list[dict] = []
    retained = [
        q
        for q in packet.get("quotes", [])
        if not (
            q.get("country_code", "") == locale.country_code
            and any(q["ref_id"] == t["id"] for t in targets)
        )
    ]
    packet["quotes"] = retained
    async with httpx.AsyncClient(timeout=_PROVIDER_TIMEOUT_S) as client:
        for line in targets:
            if not _priceable(line, threshold):
                continue
            is_book = "title" in line
            result: dict = {
                "ref_id": line["id"],
                "locale": locale.model_dump(),
                "pricing": {"status": "not_configured", "offers": [], "providers": {}},
            }
            if is_book and settings.enable_catalogue_lookup:
                try:
                    result["identification"] = await _catalogue(line, client)
                except (httpx.HTTPError, ValueError, KeyError) as exc:
                    result["identification"] = {
                        "status": "unavailable",
                        "error": type(exc).__name__,
                    }
                if result["identification"].get("status") == "author_as_title":
                    demote_author_title(line, result["identification"])
                    results.append(result)
                    print(
                        f"[DEBUG {stamp()}] research.line.demoted ref_id={line['id']} "
                        f"author={result['identification']['author']}",
                        flush=True,
                    )
                    continue
            for provider in providers:
                started = time.perf_counter()
                lookup = provider.get_book_quotes if is_book else provider.get_item_quotes
                try:
                    outcome = await lookup(line, locale, client)
                except (httpx.HTTPError, ValueError, KeyError) as exc:
                    outcome = {
                        "status": "unavailable",
                        "quotes": [],
                        "error": type(exc).__name__,
                    }
                    print(
                        f"[WARN {stamp()}] research.provider.unavailable provider={provider.name} ref_id={line['id']} error_type={type(exc).__name__}",
                        flush=True,
                    )
                elapsed = round(time.perf_counter() - started, 3)
                _record_cost(packet, provider.name, int(outcome.get("calls", 1)), elapsed)
                quotes = outcome.get("quotes", [])
                packet["quotes"].extend(quotes)
                result["pricing"]["offers"].extend(quotes)
                result["pricing"]["providers"][provider.name] = {
                    **{k: v for k, v in outcome.items() if k != "quotes"},
                    "quotes": len(quotes),
                    "elapsed_s": elapsed,
                }
            if providers:
                result["pricing"]["status"] = (
                    "candidates" if result["pricing"]["offers"] else "not_found"
                )
            results.append(result)
            print(
                f"[DEBUG {stamp()}] research.line.finished ref_id={line['id']} quotes={len(result['pricing']['offers'])}",
                flush=True,
            )
        if settings.enable_fx_conversion:
            currencies = {q["currency"] for q in packet["quotes"] if q.get("currency")}
            calls = await ensure_rates(packet, currencies, locale.currency, client)
            if calls:
                _record_cost(packet, "fx", calls, 0.0)
    previous = [
        r
        for r in packet.get("research", [])
        if r["ref_id"] not in {r2["ref_id"] for r2 in results}
        or r.get("locale", {}).get("country_code") != locale.country_code
    ]
    packet["research"] = previous + results
    return results


COMPARISON_BOOKS = 10


def comparison_books(packet: dict, limit: int = COMPARISON_BOOKS) -> list[dict]:
    """Identified, non-appraisal books with the strongest identification first."""
    books = [b for b in packet["books"] if b.get("title") and b.get("status") != "needs_appraisal"]
    books.sort(key=lambda b: (b.get("id_confidence", 0), b.get("title", "")), reverse=True)
    return books[:limit]


def _figures(line: dict) -> dict:
    def pick(price: dict | None) -> dict | None:
        if not price or price.get("amount") is None:
            return None
        return {
            "amount": price["amount"],
            "currency": price.get("currency", ""),
            "source": price.get("source", ""),
            "url": price.get("url", ""),
            "retrieved_at": price.get("retrieved_at", ""),
            "converted": bool(price.get("converted")),
            "condition_assumed": price.get("condition_assumed", ""),
        }

    return {
        "replacement_cost": pick(line.get("replacement_cost")),
        "used_value": pick(line.get("used_value")),
    }


async def compare_locale(
    packet: dict, target: Locale, providers: list[PriceProvider] | None = None
) -> dict:
    """Research and value up to ten identified books in `target`; store the comparison."""
    base = sweep_locale(packet)
    chosen = comparison_books(packet)
    ids = [b["id"] for b in chosen]
    snapshot = copy.deepcopy(packet)
    snapshot["sweep"].update(
        country=target.country,
        currency=target.currency,
        country_code=target.country_code,
    )
    snapshot_books = [b for b in snapshot["books"] if b["id"] in ids]
    await research_inventory(snapshot, providers, locale=target, lines=snapshot_books)
    apply_prices(snapshot, country=target.country, currency=target.currency, write=True)
    valued = {b["id"]: b for b in snapshot["books"]}
    rows = [
        {
            "ref_id": book["id"],
            "title": book.get("title", ""),
            "author": book.get("author", ""),
            base.currency: _figures(book),
            target.currency: _figures(valued[book["id"]]),
        }
        for book in chosen
    ]
    result = {
        "generated_at": datetime.now(UTC).isoformat(),
        "base": base.model_dump(),
        "comparison": target.model_dump(),
        "book_ids": ids,
        "rows": rows,
        "quotes_retrieved": len(
            [q for q in snapshot.get("quotes", []) if q.get("country_code") == target.country_code]
        ),
        "fx_rates": {
            k: v
            for k, v in snapshot.get("fx_rates", {}).items()
            if k.endswith("->" + target.currency)
        },
        "price_details": {ref: snapshot.get("price_details", {}).get(ref) for ref in ids},
        "note": "The same providers were queried for the second market; quotes in another currency were converted with the dated FX evidence listed here. Books without a retrievable local or convertible quote stay blank.",
    }
    packet["locale_comparison"] = result
    # The snapshot started from this packet's counters, so its totals already include
    # the comparison calls.
    if "provider_calls" in snapshot.get("cost", {}):
        packet.setdefault("cost", {})["provider_calls"] = snapshot["cost"]["provider_calls"]
    return result
