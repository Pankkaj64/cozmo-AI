import copy
import io
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch
from PIL import Image
from fastapi.testclient import TestClient
from app import main
from app.agent import refresh_workflow
from app.conversation import respond, Turn
from app.measurement import Calibration, RoomInput, measure_spine, room_geometry
from app.pricing import Offer, apply_prices
from app.packet import calculate_totals, report_html
from app.schemas import empty_packet


def packet():
    result = empty_packet('unit-test', 'United Arab Emirates', 'AED')
    result['books'] = [{'id':'b1','shelf':'Shelf 1','title':'Test book','author':'','frame_ref':'data/frames/test.jpg','id_confidence':.9,'status':'identified','spine_height_cm':None,'spine_thickness_cm':None}]
    result['items'] = [{'id':'i1','category':'portrait','description':'Framed portrait','shelf':'Shelf 1','frame_ref':'data/frames/test.jpg'}]
    result['frames'] = [{'frame_ref':'data/frames/test.jpg','shelf':'Shelf 1','vision_status':'ok','primary_count':1,'ocr_text':['Test book'],'quality':{'width':1000,'height':500}}]
    return result


def offer(**overrides):
    return Offer.model_validate(dict(ref_id='b1',kind='replacement',country='United Arab Emirates',currency='AED',amount=100,source='Unit test retailer',url='https://example.com/book',retrieved_at=date.today().isoformat(),condition_assumed='New physical paperback',match_basis='Exact physical edition checked',verified=True,**overrides))


class WorkflowTests(unittest.TestCase):
    def test_rectangle_polygon_and_shelving_geometry(self):
        rectangle = room_geometry(RoomInput(length_m=4,width_m=3,height_m=2.5,shelving=[(2,2)],source='known plan',frame_ref='frame'))
        self.assertEqual(rectangle['floor_area_m2'],12)
        self.assertEqual(rectangle['wall_area_m2'],35)
        self.assertAlmostEqual(rectangle['floor_area_ft2'],129.1669,places=4)
        polygon = room_geometry(RoomInput(height_m=3,polygon_m=[(0,0),(4,0),(4,2),(2,2),(2,4),(0,4)],source='lidar polygon',frame_ref='frame'))
        self.assertEqual(polygon['floor_area_m2'],12)
        self.assertEqual(polygon['wall_area_m2'],48)

    def test_rejects_crossed_or_impossible_geometry(self):
        with self.assertRaises(ValueError):
            RoomInput(height_m=3,polygon_m=[(0,0),(4,4),(0,4),(4,0)],source='known plan',frame_ref='frame')
        with self.assertRaises(ValueError):
            room_geometry(RoomInput(length_m=1,width_m=1,height_m=1,shelving=[(10,10)],source='known plan',frame_ref='frame'))

    def test_reference_scale_handles_horizontal_and_vertical_spines(self):
        c=Calibration(frame_ref='f',start={'x':0,'y':0},end={'x':.5,'y':0},length_cm=50,reference='Known shelf width',same_plane=True,front_on=True)
        vertical=measure_spine([.1,.1,.13,.5],c,1000,500)
        horizontal=measure_spine([.1,.1,.3,.16],c,1000,500)
        for value in (vertical,horizontal):
            self.assertEqual(value['spine_height_cm'],20)
            self.assertEqual(value['spine_thickness_cm'],3)
        with self.assertRaises(ValueError): measure_spine([0,0,2,1],c,1000,500)

    def test_local_quotes_and_currency_conversion_are_not_silently_mixed(self):
        p=packet(); local=offer().model_dump(mode='json')
        foreign={**local,'country':'United Kingdom','currency':'GBP','amount':10}
        p['offers']=[foreign]
        apply_prices(p);self.assertIsNone(p['books'][0]['replacement_cost']['amount'])
        p['offers']=[{**foreign,'target_currency':'AED','fx_rate':4.8,'fx_url':'https://example.com/fx','fx_date':date.today().isoformat()}]
        apply_prices(p);self.assertEqual(p['books'][0]['replacement_cost']['amount'],48)
        self.assertTrue(p['books'][0]['replacement_cost']['converted'])
        p['offers'].append(local);apply_prices(p)
        self.assertEqual(p['books'][0]['replacement_cost']['amount'],100)
        clone=copy.deepcopy(p); comparison=apply_prices(clone,country='United Kingdom',currency='GBP')
        self.assertEqual(p['books'][0]['replacement_cost']['amount'],100)
        self.assertEqual(comparison['currency'],'GBP')

    def test_invalid_price_evidence_is_rejected(self):
        base=offer().model_dump(mode='json')
        for change in ({'url':'javascript:alert(1)'},{'amount':-2},{'target_currency':'USD'},{'verified':False},{'high':2},{'amount':float('nan')}):
            with self.assertRaises(ValueError): Offer.model_validate({**base,**change})

    def test_art_special_editions_and_high_value_are_never_auto_priced(self):
        p=packet();q=offer().model_dump(mode='json');p['offers']=[q,{**q,'kind':'item','ref_id':'i1'}]
        p['books'][0]['edition']='Signed first edition'
        apply_prices(p)
        self.assertIsNone(p['books'][0]['replacement_cost']['amount'])
        self.assertIsNone(p['items'][0]['replacement_cost']['low'])
        p['books'][0]['edition']='';p['offers'][0]['amount']=2500;apply_prices(p)
        self.assertEqual(p['books'][0]['status'],'needs_appraisal')

    def test_corrections_require_target_and_are_idempotent(self):
        p=packet();r=respond(p,Turn(text='that is a first edition'))
        self.assertIn('Select',r['reply']);self.assertNotIn('edition',p['books'][0])
        turn=Turn(text='that is a first edition',ref_id='b1',turn_id='turn-1')
        respond(p,turn);n=len(p['transcript']);respond(p,turn)
        self.assertEqual(len(p['transcript']),n)
        self.assertEqual(p['books'][0]['status'],'needs_appraisal')

    def test_skip_removes_owned_totals_and_preserves_evidence(self):
        p=packet();respond(p,Turn(text='skip this shelf',shelf='Shelf 1'))
        self.assertEqual(p['totals']['book_count'],0)
        self.assertEqual(len(p['excluded_inventory']),2)
        self.assertEqual(len(p['frames']),1)

    def test_clear_negative_claimant_statement_does_not_change_edition(self):
        p=packet();respond(p,Turn(text='that is not a first edition',ref_id='b1'))
        self.assertNotIn('edition',p['books'][0])
        self.assertTrue(any('needs review' in q['reason'] for q in p['review_queue']))

    def test_totals_count_excluded_lines_and_report_escapes_untrusted_text(self):
        p=packet();p['books'][0]['title']='<script>alert(1)</script>'
        refresh_workflow(p)
        self.assertEqual(p['totals']['excluded_from_totals'],2)
        report=report_html(p)
        self.assertNotIn('<script>',report)
        self.assertIn('No verified book identities yet.',report)
        self.assertNotIn('Non-book contents',report)
        self.assertNotIn('Test book', report)

    def test_complete_api_flow_and_restart_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);frames=root/'data'/'frames';packets=root/'data'/'packets';frames.mkdir(parents=True);packets.mkdir()
            image=io.BytesIO();Image.new('RGB',(1000,500),'gray').save(image,format='JPEG')
            candidate={'vision_status':'ok','primary_count':1,'books':[{'title':'Test book','author':'','confidence':.9,'description':'Black spine on left','bbox':[.1,.1,.13,.5]}],'items':[],'notes':[],'ocr_text':['Test book'],'validation':{'count':1,'agrees':True}}
            with patch.object(main,'ROOT',root),patch.object(main,'FRAME_DIR',frames),patch.object(main,'PACKET_DIR',packets),patch.object(main,'SWEEPS',{}),patch.object(main,'inspect_frame',new=AsyncMock(return_value=candidate)),TestClient(main.app) as client:
                sid=client.post('/api/sweeps',json={'country':'United Arab Emirates','currency':'AED'}).json()['sweep_id'];base=f'/api/sweeps/{sid}'
                data=client.post(base+'/frames',files={'image':('frame.jpg',image.getvalue(),'image/jpeg')},data={'shelf':'Shelf 1'}).json()
                ref=data['frame_ref'];bookid=data['packet']['books'][0]['id']
                self.assertEqual(client.post(base+'/calibration',json={'frame_ref':ref,'start':{'x':0,'y':0},'end':{'x':.5,'y':0},'length_cm':50,'reference':'Known shelf width','same_plane':True,'front_on':True}).status_code,200)
                self.assertEqual(client.post(base+'/room',json={'frame_ref':ref,'length_m':4,'width_m':3,'height_m':2.5,'source':'Known room plan'}).status_code,200)
                self.assertEqual(client.post(base+'/offers',json={**offer().model_dump(mode='json'),'ref_id':bookid}).status_code,200)
                client.post(base+'/stop-capture')
                result=client.post(base+'/finish').json()
                self.assertEqual(result['packet']['books'][0]['spine_height_cm'],20)
                self.assertEqual(result['packet']['totals']['books_replacement_cost'],100)
                self.assertTrue((root/result['report_file']).exists())
                self.assertEqual(client.post(base+'/frames',files={'image':('frame.jpg',image.getvalue(),'image/jpeg')}).status_code,409)
                main.SWEEPS.clear();main.restore_active_sweeps()
                self.assertEqual(client.get(base).json()['totals']['books_replacement_cost'],100)


if __name__ == '__main__': unittest.main()
