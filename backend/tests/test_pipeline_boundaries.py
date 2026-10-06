"""Stage contracts must hold without loading models or calling retailers."""
import unittest
from app.identification import identify_observation
from app.research import research_inventory
from app.schemas import empty_packet
from app.validation import validate_packet


class PipelineTests(unittest.TestCase):
    def test_generated_title_and_unverified_proposals_cannot_establish_identity(self):
        for observation, text, check in [
            ({'title': 'Invented title', 'confidence': .99}, 'Unreadable', {'agrees': True}),
            ({'title': 'Visible title', 'confidence': .99, 'identity_verified': False}, 'Visible title', {'agrees': True}),
            ({'title': 'Visible title', 'confidence': .99}, 'Visible title', {'agrees': False}),
        ]:
            self.assertEqual(identify_observation(observation, text, check)['status'], 'unidentified')
        result = identify_observation({'title': 'Visible title', 'confidence': .9}, 'Visible title', {'agrees': True})
        self.assertEqual(result['status'], 'identified')
        self.assertEqual(result['isbn'], '')
        self.assertEqual(result['edition'], '')

    def test_validation_is_idempotent_and_preserves_perception_findings(self):
        packet = empty_packet('test', 'United Kingdom', 'GBP')
        packet['review_queue'] = [{'ref_id': 'frame', 'reason': 'Count disagreement'}]
        first = list(validate_packet(packet))
        self.assertEqual(validate_packet(packet), first)
        self.assertIn({'ref_id': 'frame', 'reason': 'Count disagreement'}, first)
        self.assertTrue(any(row['ref_id'] == 'room' for row in first))


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_injected_provider_receives_locale_and_skips_appraisal(self):
        calls = []
        class Provider:
            async def get_book_prices(self, book, country, currency):
                calls.append((book['id'], country, currency))
                return {'status': 'not_found', 'offers': []}
            async def get_item_prices(self, item, country, currency):
                calls.append((item['id'], country, currency))
                return {'status': 'not_found', 'offers': []}
        packet = empty_packet('test', 'United Kingdom', 'GBP')
        packet['books'] = [{'id': 'book', 'title': 'Observed work'}, {'id': 'rare', 'title': 'Rare work', 'edition': 'signed'}]
        packet['items'] = [{'id': 'lamp', 'category': 'lamp'}]
        result = await research_inventory(packet, Provider())
        self.assertEqual(calls, [('book', 'United Kingdom', 'GBP'), ('lamp', 'United Kingdom', 'GBP')])
        self.assertEqual(len(result), 2)
        self.assertNotIn('replacement_cost', packet['books'][0])


class StageTimingTests(unittest.IsolatedAsyncioTestCase):
    async def test_failure_keeps_duration_and_does_not_claim_completion(self):
        from app.pipeline import pipeline_stage
        result = {}
        with self.assertRaises(RuntimeError):
            async with pipeline_stage(result, 'ocr_spine_reading'):
                raise RuntimeError('reader unavailable')
        record = result['pipeline_stages'][0]
        self.assertEqual(record['status'], 'failed')
        self.assertGreaterEqual(record['elapsed_s'], 0)
        self.assertEqual(record['error_type'], 'RuntimeError')
