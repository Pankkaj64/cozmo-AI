import copy
import unittest
from unittest.mock import AsyncMock, patch
from app import local_detector as detector, vision
from app.agent import merge_observations, refresh_workflow
from app.schemas import empty_packet


class LocalDetectorTests(unittest.IsolatedAsyncioTestCase):
    def test_localized_books_deduplicate_and_exclude_person_and_overlapping_laptop(self):
        box = [.1,.2,.4,.8]
        objects = [dict(category=c,confidence=p,bbox=box) for c,p in [('book',.8),('book',.7),('laptop',.9),('person',.99)]]
        objects.append(dict(category='chair',confidence=.8,bbox=[.6,.2,.8,.6]))
        books,items = detector.select_objects(objects)
        self.assertEqual(len(books),1)
        self.assertEqual([i['category'] for i in items],['chair'])

    def test_edge_candidate_is_retained_but_marked_partial(self):
        books,_=detector.select_objects([dict(category='book',confidence=.4,bbox=[.1,.5,.6,1])])
        self.assertTrue(books[0]['partial'])

    def test_equal_counts_at_different_locations_do_not_validate(self):
        primary=[dict(confidence=.9,bbox=[.1,.1,.3,.4])]
        secondary=[dict(confidence=.9,bbox=[.6,.6,.9,.9])]
        self.assertFalse(detector.compare_boxes(primary,secondary)['agrees'])
        self.assertTrue(detector.compare_boxes(primary,primary)['agrees'])
        self.assertTrue(detector.compare_boxes([],[])['agrees'])

    def test_two_primary_boxes_cannot_match_one_secondary_box(self):
        book=dict(confidence=.9,bbox=[.1,.1,.3,.4])
        self.assertFalse(detector.compare_boxes([book,book],[book])['agrees'])

    def test_roles_cannot_invent_or_cross_assign_text(self):
        books=[{'ocr_lines':[{'text':'रावण'},{'text':'अमीश'}]}]
        good={'books':[{'index':0,'title_lines':[0],'author_lines':[1]}]}
        self.assertEqual(detector.apply_roles(copy.deepcopy(books),good)[0]['title'],'रावण')
        for change in ({'title_lines':[2]}, {'title_lines':[True]}, {'author_lines':[0]}, {'title_lines':[0,0]}, {'index':-1}):
            bad={'books':[{**good['books'][0],**change}]}
            with self.assertRaises(ValueError): detector.apply_roles(copy.deepcopy(books),bad)
        with self.assertRaises(ValueError): detector.apply_roles(copy.deepcopy(books),{'books':[]})

    async def test_disagreement_retains_boxes_and_ocr_without_claiming_identity(self):
        objects=[dict(category='book',confidence=.95,bbox=[.1,.1,.4,.8])]
        def crops(raw,books):
            for book in books: book.update(ocr_lines=[{'text':'Test title'}],ocr_errors=[])
            return books
        async def roles(books):
            return [dict(b,title='Test title',author='') for b in books],'proposed'
        with patch.object(detector,'detect_objects',return_value=objects),patch.object(detector,'read_book_crops',side_effect=crops),patch.object(detector,'select_roles',side_effect=roles),patch.object(detector,'validate_boxes',new=AsyncMock(return_value={'count':2,'agrees':False})):
            result=await detector.inspect_local_frame(b'raw','frame.jpg')
        self.assertEqual(result['vision_status'],'ok')
        self.assertEqual(result['count_status'],'needs_review')
        self.assertEqual(len(result['books']),1)
        state={'packet':empty_packet('test'), 'position':0}
        merge_observations(state,result,'Shelf 1','frame.jpg')
        book=state['packet']['books'][0]
        self.assertEqual(book['proposed_title'],'Test title')
        self.assertEqual(book['title'],'')
        self.assertEqual(book['object_bbox'],objects[0]['bbox'])
        merge_observations(state,result,'Shelf 1','frame2.jpg')
        self.assertEqual(len(state['packet']['books']),1)

    def test_bestseller_slogan_cannot_become_a_publisher(self):
        result=detector.validated_reading({'is_book':True,'title':'Visible work','author':'A Writer','publisher':'NTERNATIONAL BESTSA'},['','A Writer','NTERNATIONAL BESTSA'])
        self.assertEqual(result['author'],'A Writer')
        self.assertEqual(result['publisher'],'')
        self.assertIn('publisher_rejected',result)

    async def test_visual_reader_cannot_publish_author_text_from_outside_its_crop(self):
        import io
        from PIL import Image
        from unittest.mock import MagicMock
        raw=io.BytesIO();Image.new('RGB',(100,100),'white').save(raw,format='JPEG')
        response=MagicMock()
        response.json.return_value={'done_reason':'stop','message':{'content':'{"is_book":true,"title":"Visible title","author":"Invented name","publisher":"Visible Press"}'}}
        client=AsyncMock();client.post.return_value=response
        with patch.object(detector.httpx,'AsyncClient') as factory:
            factory.return_value.__aenter__=AsyncMock(return_value=client)
            factory.return_value.__aexit__=AsyncMock(return_value=False)
            result=await detector.read_visual_title(raw.getvalue(),{'bbox':[0,0,1,1],'ocr_lines':[{'text':'Visible title'},{'text':'Visible Press'}]})
        self.assertEqual(result['author'],'')
        self.assertEqual(result['publisher'],'Visible Press')

    async def test_secondary_recovery_does_not_create_fake_independent_agreement(self):
        book=dict(category='book',confidence=.8,bbox=[.1,.1,.4,.8])
        def objects(raw,model_name=detector.DETECTOR_MODEL):
            return [book] if model_name == detector.VALIDATOR_DETECTOR_MODEL else []
        validator=AsyncMock(return_value={'count':1,'agrees':False})
        with patch.object(detector,'detect_objects',side_effect=objects), patch.object(detector,'read_book_crops',side_effect=lambda raw,books:books), patch.object(detector,'validate_boxes',new=validator):
            result=await detector.inspect_local_frame(b'raw','frame.jpg')
        self.assertEqual(result['primary_count'],0)
        self.assertEqual(result['candidate_count'],1)
        validator.assert_awaited_once_with(b'raw',[])
        self.assertEqual(result['count_status'],'needs_review')

    def test_room_categories_keep_furnishings_but_exclude_structural_classes_and_people(self):
        objects=[dict(category=c,confidence=.9,bbox=[n*.1, .1, n*.1+.08, .5]) for n,c in enumerate(['bookshelf','lamp','rug','framed painting','coffee machine','door','window','person'])]
        _,items=detector.select_objects(objects)
        self.assertEqual([i['category'] for i in items],['bookshelf','lamp','rug','framed painting','coffee machine'])

    async def test_ocr_failure_keeps_detected_books(self):
        objects=[dict(category='book',confidence=.8,bbox=[.1,.1,.4,.8])]
        with patch.object(detector,'detect_objects',return_value=objects),patch.object(detector,'read_book_crops',side_effect=RuntimeError('OCR unavailable')),patch.object(detector,'validate_boxes',new=AsyncMock(return_value={'count':1,'agrees':True})):
            result=await detector.inspect_local_frame(b'raw','frame.jpg')
        self.assertEqual(len(result['books']),1)
        self.assertEqual(result['ocr_status'],'partial')

    async def test_detector_failure_is_not_reported_as_zero_books(self):
        with patch.object(detector,'detect_objects',side_effect=FileNotFoundError('weights')):
            result=await detector.inspect_local_frame(b'raw','frame.jpg')
        self.assertEqual(result['vision_status'],'failed')
        self.assertNotIn('primary_count',result)

    def test_object_box_is_never_measured_as_spine(self):
        state={'packet':empty_packet('test'), 'position':0}
        candidate={'vision_status':'ok','books':[{'confidence':.9,'title':'','bbox':[.1,.1,.4,.8],'bbox_kind':'object'}],'items':[]}
        p=merge_observations(state,candidate,'Shelf 1','frame.jpg')
        p['calibrations']={'frame.jpg':{'frame_ref':'frame.jpg','start':{'x':0,'y':0},'end':{'x':.5,'y':0},'length_cm':50,'reference':'shelf','same_plane':True,'front_on':True}}
        p['frames'][0]['quality']={'width':1000,'height':500}
        refresh_workflow(p)
        self.assertIsNone(p['books'][0]['spine_height_cm'])
        p['books'][0]['bbox_kind']='spine'
        refresh_workflow(p)
        self.assertIsNotNone(p['books'][0]['spine_height_cm'])

if __name__=='__main__': unittest.main()
