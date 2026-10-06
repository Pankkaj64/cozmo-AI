import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from app.bundle import claim_bundle
from app.identification import visible_isbn
from app.schemas import empty_packet
from app.agent import refresh_workflow


class DeliveryTests(unittest.TestCase):
    def test_isbn_requires_visible_label_and_checksum(self):
        self.assertEqual(visible_isbn('ISBN: 978-0-306-40615-7'), '9780306406157')
        self.assertEqual(visible_isbn('ISBN 0-306-40615-2'), '0306406152')
        self.assertEqual(visible_isbn('9780306406157'), '')
        self.assertEqual(visible_isbn('ISBN: 9780306406158'), '')

    def test_bundle_contains_canonical_packet_and_original_evidence(self):
        packet = empty_packet('test', 'United Kingdom', 'GBP')
        packet['frames'] = [{'frame_ref': 'data/frames/frame.jpg', 'shelf': 'Shelf 1'}]
        packet['books'] = [{'id': 'book', 'title':'Observed title', 'status':'identified', 'crop_verification':{'agreed':True}, 'frame_ref': 'data/frames/frame.jpg', 'shelf': 'Shelf 1'}]
        refresh_workflow(packet)
        with tempfile.TemporaryDirectory() as directory:
            frame = Path(directory) / 'data/frames/frame.jpg'
            frame.parent.mkdir(parents=True)
            frame.write_bytes(b'evidence')
            with zipfile.ZipFile(io.BytesIO(claim_bundle(packet, directory))) as archive:
                self.assertEqual(archive.read('data/frames/frame.jpg'), b'evidence')
                self.assertEqual(json.loads(archive.read('data/packets/claim_packet.json'))['materials'][0]['name'], 'Observed title')
                self.assertEqual(len(json.loads(archive.read('manifest.json'))['evidence']), 1)
                self.assertIn('../frames/frame.jpg', archive.read('data/packets/report.html').decode())
            packet['frames'][0]['frame_ref'] = 'data/frames/../../../secret'
            with self.assertRaises(ValueError): claim_bundle(packet, directory)

    def test_unknown_model_requires_sourced_range(self):
        from app.pricing import apply_prices
        from datetime import date
        packet = empty_packet('test', 'United Kingdom', 'GBP')
        packet['items'] = [{'id': 'lamp', 'category': 'lamp', 'brand_model': ''}]
        offer = {'ref_id': 'lamp', 'kind': 'item', 'country': 'United Kingdom', 'currency': 'GBP', 'amount': 20,
                 'source': 'Test fixture', 'url': 'https://example.com/lamp', 'retrieved_at': date.today().isoformat(),
                 'condition_assumed': 'New', 'match_basis': 'Comparable material and dimensions', 'verified': True}
        packet['offers'] = [offer]
        apply_prices(packet)
        self.assertIsNone(packet['items'][0]['replacement_cost']['low'])
        packet['offers'][0]['high'] = 40
        apply_prices(packet)
        self.assertEqual(packet['items'][0]['status'], 'range')
        self.assertEqual(packet['items'][0]['replacement_cost']['high'], 40)

    def test_live_identity_requires_image_ocr_agreement(self):
        from app.identification import reading_agreement
        book = {'title': 'Observed title', 'ocr_lines': [{'text': 'Observed title', 'confidence': .95}],
                'reader_evidence': {'title': 'Observed title', 'is_book': True}}
        self.assertTrue(reading_agreement(book)[0])
        book['reader_evidence']['title'] = 'Invented title'
        self.assertFalse(reading_agreement(book)[0])
        book['reader_evidence']['title'] = 'Observed title'
        book['ocr_lines'][0]['confidence'] = .5
        self.assertFalse(reading_agreement(book)[0])

    def test_settings_evaluation_and_bundle_api(self):
        from fastapi.testclient import TestClient
        from unittest.mock import patch
        from app import main
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            packets = root / 'data/packets'; packets.mkdir(parents=True)
            (root / 'data/frames').mkdir()
            with patch.object(main, 'ROOT', root), patch.object(main, 'PACKET_DIR', packets), patch.object(main, 'SWEEPS', {}), TestClient(main.app) as client:
                sid = client.post('/api/sweeps', json={'country':'United Kingdom','currency':'GBP','appraisal_threshold':500}).json()['sweep_id']
                base = '/api/sweeps/' + sid
                with patch.object(main, 'EVENT_STREAM_LIFETIME_S', 0):
                    events = client.get(base + '/events')
                    self.assertEqual(events.status_code, 200)
                    self.assertIn('retry: 1500', events.text)
                self.assertEqual(client.post(base + '/settings', json={'appraisal_threshold':1000}).json()['packet']['appraisal_threshold'],1000)
                self.assertEqual(client.post(base + '/settings', json={'appraisal_threshold':-1}).status_code,422)
                result = client.post(base + '/evaluate', json={'ground_truth':{'books':[],'items':[],'room':{}}})
                self.assertEqual(result.status_code,200)
                self.assertFalse(result.json()['evaluation']['all_pass'])
                response = client.get(base + '/bundle')
                self.assertEqual(response.status_code,200)
                with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                    self.assertIn('ground_truth.json',archive.namelist())
                    self.assertIn('results.json',archive.namelist())


class SourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_both_book_markets_are_requested(self):
        import httpx
        from unittest.mock import patch
        from app.research import EbayPricingService
        requests = []
        def reply(request):
            requests.append(str(request.url))
            return httpx.Response(200, json={'itemSummaries':[]})
        with patch.dict('os.environ', {'EBAY_ACCESS_TOKEN':'test-fixture-not-a-secret'}):
            async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
                result = await EbayPricingService(client, 'GB').get_book_prices({'id':'b','title':'Observed title'}, 'United Kingdom','GBP')
        self.assertEqual(len(requests),2)
        self.assertIn('replacement',result['attempts'])
        self.assertIn('used',result['attempts'])
        self.assertEqual(result['offers'],[])
