import unittest
from app.materials import identified_materials
from app.packet import report_html
from app.schemas import empty_packet


class MaterialTests(unittest.TestCase):
    def test_named_books_and_objects_share_concise_export(self):
        packet = empty_packet('test')
        packet['books'] = [{'id':'b','title':'Observed title','author':'Observed author','status':'identified','crop_verification':{'agreed':True},'frame_ref':'data/frames/b.jpg'}, {'id':'unreadable'}, {'id':'falsebook','proposed_title':'Wrong','reader_evidence':{'is_book':False}}]
        packet['items'] = [{'id':'lamp','category':'lamp','material':'metal','category_verified':True,'crop_verification':{'agreed':True},'frame_ref':'data/frames/l.jpg'}, {'id':'unknown','category':'unknown'}, {'id':'wall','category':'wall'}, {'id':'false','category':'chair','dismissed_by_reader':True}]
        result = identified_materials(packet)
        self.assertEqual(list(result), ['materials'])
        self.assertEqual([row['name'] for row in result['materials']], ['Observed title','lamp'])
        self.assertEqual(result['materials'][0]['author'],'Observed author')
        self.assertEqual(result['materials'][0]['status'],'identified')
        self.assertEqual(result['materials'][1]['material'],'metal')
        report = report_html(packet)
        self.assertIn('Other identified contents', report)
        self.assertIn('lamp', report)
        self.assertNotIn('Wrong', report)


class ClassificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_nonbook_crop_uses_room_object_name(self):
        from unittest.mock import patch, AsyncMock
        from app import local_detector
        book = {'bbox':[.1,.1,.4,.5], 'confidence':.9, 'partial':False,
                'ocr_lines':[{'text':'visible text'}], 'ocr_errors':[]}
        candidate = {'notes':[], 'object_alternatives':[{'bbox':[.1,.1,.4,.5], 'confidence':.9, 'category':'coffee machine'}]}
        with patch.object(local_detector, 'read_book_crops', return_value=[book]), patch.object(local_detector, 'select_roles', new=AsyncMock(return_value=([book],'proposed'))), patch.object(local_detector, 'read_visual_title', new=AsyncMock(return_value={'is_book':False,'title':'','author':'','publisher':''})):
            books, items = await local_detector.read_frame_spines(b'fixture', [book], [], candidate)
        self.assertEqual(books, [])
        self.assertEqual(items[0]['category'], 'coffee machine')
