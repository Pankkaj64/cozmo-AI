"""Check that the configured price sources answer with real data. Run after filling .env.

    python tools/check_price_sources.py        # "Zero to One", Peter Thiel, United Kingdom
    python tools/check_price_sources.py AE     # same book, another country code

Prints one line per source (eBay, Google Books) and the FX rate, then OK or what is missing.
"""

import asyncio
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.logic.pricing import resolve_locale  # noqa: E402
from app.logic.providers import configured_providers, fetch_rate  # noqa: E402

BOOK = {"id": "check", "title": "Zero to One", "author": "Peter Thiel"}


async def main() -> int:
    locale = resolve_locale(country_code=sys.argv[1] if len(sys.argv) > 1 else "GB")
    print(f"locale: {locale.country} {locale.currency} marketplace={locale.ebay_marketplace}")
    providers = configured_providers()
    if not providers:
        print(
            "no price source configured: set EBAY_CLIENT_ID + EBAY_CLIENT_SECRET, or a Google key"
        )
        return 1
    failures = 0
    async with httpx.AsyncClient(timeout=30) as client:
        for provider in providers:
            try:
                res = await provider.get_book_quotes(BOOK, locale, client)
                quotes = res.get("quotes", [])
                print(f"{provider.name}: status={res.get('status')} quotes={len(quotes)}")
                for q in quotes[:4]:
                    print(
                        f"   {q.get('kind', '?'):12} {q.get('amount')} {q.get('currency')} "
                        f"condition={q.get('condition', '')} match={q.get('match_score')} "
                        f"{str(q.get('url', ''))[:60]}"
                    )
                if not quotes:
                    failures += 1
                    print(
                        f"   detail: {str({k: v for k, v in res.items() if k != 'quotes'})[:300]}"
                    )
            except Exception as exc:  # noqa: BLE001 - diagnostic output
                failures += 1
                print(f"{provider.name}: FAILED {type(exc).__name__}: {str(exc)[:200]}")
        if settings.enable_fx_conversion:
            rate = await fetch_rate("USD", locale.currency, client)
            if rate:
                print(f"fx: USD->{locale.currency} {rate.get('rate')} ({rate.get('fx_date')})")
            else:
                failures += 1
                print("fx: no source answered")
    print("OK: every source answered" if not failures else f"{failures} source(s) returned nothing")
    return failures


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
