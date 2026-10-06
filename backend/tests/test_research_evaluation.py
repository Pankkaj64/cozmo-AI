import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch
import httpx
from app.research import resolve_work, market_candidates

spec=importlib.util.spec_from_file_location('evaluation',Path(__file__).resolve().parents[1]/'tools'/'evaluate.py')
evaluation=importlib.util.module_from_spec(spec);spec.loader.exec_module(evaluation)

class ResearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_work_match_does_not_invent_edition(self):
        transport=httpx.MockTransport(lambda request: httpx.Response(200,json={'docs':[{'title':'Test Book','key':'/works/TEST','author_name':['Reader']}]}))
        async with httpx.AsyncClient(transport=transport) as client:
            result=await resolve_work({'title':'Test Book','author':'Reader'},client)
        self.assertEqual(result['status'],'work_match');self.assertFalse(result['edition_resolved'])
        self.assertNotIn('isbn',result)

    async def test_market_lookup_uses_locale_and_keeps_candidate_unverified(self):
        def mock(request):
            self.assertIn('deliveryCountry:AE',request.url.params['filter'])
            self.assertEqual(request.headers['x-ebay-c-marketplace-id'],'EBAY_GB')
            return httpx.Response(200,json={'itemSummaries':[{'title':'Comparable','itemWebUrl':'https://www.ebay.com/itm/123','conditionId':'1000','condition':'New','price':{'value':'12.50','currency':'GBP'},'itemLocation':{'country':'GB'}}]})
        with patch.dict('os.environ',{'EBAY_ACCESS_TOKEN':'test-token','EBAY_MARKETPLACE_ID':'EBAY_GB'}):
            async with httpx.AsyncClient(transport=httpx.MockTransport(mock)) as client:
                result=await market_candidates({'id':'1','title':'Test Book'},{'sweep':{'country':'United Arab Emirates','country_code':'AE'}},client)
        quote=result['offers'][0];self.assertTrue(quote['requires_match_review'])
        self.assertEqual(quote['currency'],'GBP');self.assertEqual(quote['seller_country'],'GB')

    def test_missing_truth_is_not_a_pass(self):
        p={'books':[],'items':[],'room':{},'sweep':{}}
        result=evaluation.evaluate(p,{})
        self.assertFalse(result['all_pass']);self.assertFalse(result['dataset_meets_minimum'])

    def test_duplicate_ground_truth_mapping_rejected(self):
        p={'books':[],'items':[],'room':{},'sweep':{}}
        with self.assertRaises(ValueError):
            evaluation.evaluate(p,{'books':[{'system_id':'x'},{'system_id':'x'}]})
