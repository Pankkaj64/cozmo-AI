"""Optional evidence retrieval. Camera images never leave the local vision service.

Open Library resolves works, not editions. eBay returns comparable asking-price
candidates, never silently promoted to an accepted valuation.
"""
import asyncio
import os
import re
from typing import Protocol
from datetime import datetime, timezone
import httpx
from .logging_utils import event
from .pricing import needs_appraisal
from .metadata import resolve_isbn


def normalized(value):
    return ' '.join(re.findall(r'\w+',value.casefold()))


async def resolve_work(book, client):
    response=await client.get('https://openlibrary.org/search.json',params={'title':book['title'],'limit':5,'fields':'key,title,author_name'},headers={'User-Agent':'LibraryContentsClaimAgent/0.2 (local research prototype)'})
    response.raise_for_status()
    candidates=[doc for doc in response.json().get('docs',[]) if normalized(doc.get('title',''))==normalized(book['title'])]
    if book.get('author'):
        candidates=[doc for doc in candidates if normalized(book['author']) in {normalized(a) for a in doc.get('author_name',[])}]
    if len(candidates)==1:
        doc=candidates[0]
        return {'status':'work_match','title':doc['title'],'authors':doc.get('author_name',[]),'source':'Open Library','url':'https://openlibrary.org/'+doc['key'].lstrip('/'),'retrieved_at':datetime.now(timezone.utc).isoformat(),'edition_resolved':False}
    return {'status':'ambiguous' if candidates else 'not_found','candidates':candidates,'edition_resolved':False}


async def market_candidates(line, packet, client, desired_kind=None):
    token=os.getenv('EBAY_ACCESS_TOKEN','')
    if not token:
        return {'status':'not_configured','reason':'Set an eBay Browse API access token, or record checked listing sources manually.','offers':[]}
    country_code=packet['sweep'].get('country_code','')
    if not country_code:
        return {'status':'needs_country_code','reason':'Enter the two-letter delivery country for market search.','offers':[]}
    marketplace=os.getenv('EBAY_MARKETPLACE_ID','EBAY_US')
    query=line.get('isbn') or line.get('title') or ' '.join(filter(None,[line.get('brand_model'),line.get('category'),line.get('description')]))
    filters = f'deliveryCountry:{country_code},buyingOptions:{{FIXED_PRICE}}'
    if desired_kind == 'replacement': filters += ',conditionIds:{1000}'
    elif desired_kind == 'used': filters += ',conditionIds:{3000|4000|5000|6000}'
    response=await client.get('https://api.ebay.com/buy/browse/v1/item_summary/search',params={'q':query[:150],'limit':5,'filter':filters},headers={'Authorization':f'Bearer {token}','X-EBAY-C-MARKETPLACE-ID':marketplace})
    response.raise_for_status()
    retrieved=datetime.now(timezone.utc).date().isoformat()
    offers=[]
    for item in response.json().get('itemSummaries',[]):
        price=item.get('price',{})
        if not price.get('value') or not item.get('itemWebUrl'): continue
        offers.append({'ref_id':line['id'],'listing_title':item.get('title',''),'amount':float(price['value']),'currency':price.get('currency'),
                       'country':packet['sweep']['country'],'seller_country':item.get('itemLocation',{}).get('country'),
                       'source':'eBay asking-price listing','url':item['itemWebUrl'],'retrieved_at':retrieved,
                       'condition_assumed':item.get('condition','Unspecified'), 'marketplace':marketplace,
                       'kind':'item' if 'category' in line else ('replacement' if item.get('conditionId')=='1000' else 'used'),
                       'requires_match_review':True,'shipping_and_tax_included':False})
    return {'status':'candidates' if offers else 'not_found','offers':offers}


class PricingService(Protocol):
    """Replaceable retrieval boundary. Results are evidence candidates, not values."""
    async def get_book_prices(self, book, country, currency): ...
    async def get_item_prices(self, item, country, currency): ...


class EbayPricingService:
    def __init__(self, client, country_code):
        self.client = client
        self.country_code = country_code

    async def get_book_prices(self, book, country, currency):
        packet = {"sweep": {"country": country, "currency": currency, "country_code": self.country_code}}
        results = await asyncio.gather(*(market_candidates(book, packet, self.client, kind)
                                        for kind in ("replacement", "used")), return_exceptions=True)
        branches = {}
        offers = []
        for kind, result in zip(("replacement", "used"), results):
            if isinstance(result, Exception):
                branches[kind] = {"status": "unavailable", "error": type(result).__name__}
            else:
                branches[kind] = result
                offers.extend(result.get("offers", []))
        return {"status": "candidates" if offers else "not_found", "offers": offers,
                "attempts": branches, "requested_currency": currency}

    async def get_item_prices(self, item, country, currency):
        result = await market_candidates(item, {"sweep": {"country": country,
            "currency": currency, "country_code": self.country_code}}, self.client)
        result["requested_currency"] = currency
        return result


async def research_inventory(packet, pricing_service: PricingService | None = None):
    results=[]
    # Bound total background research so absent/slow services cannot hold the packet forever.
    async with httpx.AsyncClient(timeout=10) as client:
        provider = pricing_service or EbayPricingService(client, packet["sweep"].get("country_code", ""))
        for line in packet['books']+packet['items']:
            if needs_appraisal(line,packet.get('appraisal_threshold',2000)):
                continue
            if 'title' in line and not line.get('title') and not line.get('isbn'): continue
            result={'ref_id':line['id']}
            try:
                if 'title' in line and os.getenv('ENABLE_CATALOGUE_LOOKUP','false').lower()=='true':
                    if line.get('isbn'):
                        metadata = await resolve_isbn(line['isbn'], client)
                        result['identification'] = metadata or {'status': 'not_found'}
                        if metadata and metadata['title']:
                            line.update({k: metadata[k] for k in ('title', 'author', 'publisher')})
                            line.update(identity_source=metadata, status='identified')
                    elif line.get('title'):
                        result['identification']=await resolve_work(line,client)
                lookup = provider.get_book_prices if 'title' in line else provider.get_item_prices
                result['pricing'] = await lookup(line, packet['sweep']['country'], packet['sweep']['currency'])
            except (httpx.HTTPError,ValueError,KeyError) as exc:
                # Do not include authenticated request headers or URLs in logs.
                result['error']=type(exc).__name__
                event('research.unavailable',level='warning',ref_id=line['id'],error_type=type(exc).__name__)
            results.append(result)
            packet['research']=results
            event('research.line.finished',ref_id=line['id'],offers=len(result.get('pricing',{}).get('offers',[])))
    return results
