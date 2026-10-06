import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.agent import merge_observations, refresh_workflow
from app.schemas import empty_packet
from app.packet import report_html
from app import main


def candidate(titles=(), items=()):
    return {'vision_status':'ok','books':[{'title':t, 'confidence':.8, 'identity_verified':False, 'bbox_kind':'object', 'bbox':[.1,.1,.4,.8], 'ocr_lines':[{'text':t}]} for t in titles], 'items':list(items), 'validation':{'agrees':True}, 'notes':[], 'ocr_text':list(titles)}


class TrackingTests(unittest.TestCase):
    def setUp(self):
        self.state={'packet':empty_packet('test'), 'position':0}

    def merge(self,c,ref='one.jpg'):
        return merge_observations(self.state, c, 'Shelf 1', ref)

    def test_later_empty_success_does_not_erase_books_or_room_items(self):
        item={'category':'chair','confidence':.8,'bbox':[.6,.2,.9,.8]}
        p=self.merge(candidate(['First book'],[item]))
        ids=[entry['id'] for entry in p['books']+p['items']]
        self.merge(candidate(), 'empty.jpg')
        self.assertEqual(ids,[entry['id'] for entry in p['books']+p['items']])
        self.assertEqual(p['frames'][0]['items'],[item])

    def test_repeated_proposals_match_without_losing_reviewed_fields_or_prices(self):
        p=self.merge(candidate(['Example title']))
        book=p['books'][0]
        book.update(title='Example title',author='Reviewed author',publisher='Reviewed publisher',identity_source={'source':'Saved crop'},replacement_cost={'amount':45})
        self.merge(candidate(['Example title']), 'two.jpg')
        self.assertEqual(len(p['books']),1)
        self.assertEqual(book['publisher'],'Reviewed publisher')
        self.assertEqual(book['replacement_cost']['amount'],45)
        self.assertEqual(len(book['observations']),2)
        self.assertEqual(book['frame_ref'],'one.jpg')

    def test_distinct_books_at_same_position_are_not_merged(self):
        p=self.merge(candidate(['First work']))
        self.merge(candidate(['Different work']), 'two.jpg')
        self.assertEqual(len(p['books']),2)

    def test_two_identical_copies_in_one_view_cannot_match_one_track(self):
        p=self.merge(candidate(['Same title']))
        self.merge(candidate(['Same title','Same title']), 'two.jpg')
        self.assertEqual(len(p['books']),2)
        self.merge(candidate(['Same title','Same title']), 'three.jpg')
        self.assertEqual(len(p['books']),2)

    def test_missing_text_does_not_invent_identity_from_position(self):
        p=self.merge(candidate(['']))
        self.merge(candidate(['']), 'two.jpg')
        self.assertEqual(len(p['books']),2) # Ambiguous candidates must be reviewed, not collapsed by position.
        self.assertFalse(any(b['title'] for b in p['books']))

    def test_later_verified_reading_promotes_an_existing_candidate(self):
        p=self.merge(candidate(['Example title']))
        verified=candidate(['Example title'])
        verified['books'][0].update(identity_verified=True, crop_verification={'agreed':True,'frame_ref':'two.jpg'})
        self.merge(verified,'two.jpg')
        self.assertEqual(len(p['books']),1)
        self.assertEqual(p['books'][0]['title'],'Example title')
        self.assertEqual(p['books'][0]['status'],'identified')

    def test_review_and_exclusion_are_audited_and_excluded_track_stays_excluded(self):
        p=self.merge(candidate(['Example title']))
        book=p['books'][0]
        with tempfile.TemporaryDirectory() as directory, patch.object(main,'PACKET_DIR',Path(directory)), patch.object(main,'SWEEPS',{'test':self.state}):
            client=TestClient(main.app)
            response=client.post('/api/sweeps/test/inventory-review',json={'ref_id':book['id'],'title':'Example title','author':'A Writer','publisher':'Example Press','source':'Readable on saved frame'})
            self.assertEqual(response.status_code,200)
            self.assertEqual(p['books'][0]['publisher'],'Example Press')
            response=client.post('/api/sweeps/test/inventory-review',json={'ref_id':book['id'],'exclude':True,'source':'Duplicate copy of prior observation'})
            self.assertEqual(response.status_code,200)
            self.assertFalse(p['books'])
            self.merge(candidate(['Example title']),'again.jpg')
            self.assertFalse(p['books'])
            self.assertEqual(len(p['excluded_inventory']),1)

    def test_room_alignment_matches_latest_observation_without_merging_two_objects(self):
        import numpy as np
        from app.tracking import match_track
        old = {'id':'chair1','category':'chair','frame_ref':'first.jpg','object_bbox':[.1,.1,.2,.4],
               'observations':[{'frame_ref':'recent.jpg','bbox':[.6,.1,.8,.7]}]}
        found = {'category':'chair','bbox':[.6,.1,.8,.7]}
        self.assertIs(match_track(found,[old],'item',{'recent.jpg':np.eye(3)}),old)
        self.assertIsNone(match_track({'category':'chair','bbox':[.1,.7,.3,.9]},[old],'item',{'recent.jpg':np.eye(3)}))

    def test_report_shows_available_readings_in_concise_columns(self):
        p=self.merge(candidate(['Visible proposed title'],[{'category':'framed painting','confidence':.8,'bbox':[.6,.2,.9,.8],'proposed_material':'wood'}]))
        refresh_workflow(p)
        html=report_html(p)
        for phrase in ['No verified book identities yet.']:
            self.assertIn(phrase,html)
        for removed in ['Unverified reading', 'Assignment feature status', 'Unknown / excluded', 'Conversation and corrections']:
            self.assertNotIn(removed, html)
        self.assertNotIn('Visible proposed title', html)
        self.assertEqual(p['totals']['item_count'],1)
        self.assertEqual(p['totals']['books_identified'],0)

if __name__=='__main__': unittest.main()
