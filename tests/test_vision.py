import base64
import io
import tempfile
import unittest
from pathlib import Path
from PIL import Image
from qqhermes.vision import prepare_image, image_request, vision_paths, MAX_SIDE, MAX_PIXELS
from qqhermes.media import image_extension


def encoded(size=(30,40),mode='RGB',fmt='JPEG'):
    im=Image.new(mode,size)
    buf=io.BytesIO();im.save(buf,format=fmt)
    return buf.getvalue()


class VisionTests(unittest.TestCase):
    def test_tall_screenshot_preserves_readable_width_in_order(self):
        im=Image.new('RGB',(1050,8274),'red')
        im.paste('blue',(0,4137,1050,8274))
        buf=io.BytesIO();im.save(buf,format='JPEG')
        tiles=prepare_image(buf.getvalue())
        self.assertEqual(len(tiles),3)
        decoded=[Image.open(io.BytesIO(x)) for x in tiles]
        self.assertEqual(sum(x.height for x in decoded),8274)
        for image in decoded:
            image.load();self.assertEqual(image.width,1050)
            self.assertLessEqual(max(image.size),MAX_SIDE)
        self.assertGreater(decoded[0].getpixel((10,10))[0],240)
        self.assertGreater(decoded[-1].getpixel((10,10))[2],240)

    def test_transparency_and_landscape_are_supported(self):
        for raw in (encoded((100,100),'RGBA','PNG'),encoded((8274,1050))):
            for tile in prepare_image(raw):
                im=Image.open(io.BytesIO(tile));im.load()
                self.assertEqual(im.mode,'RGB')
                self.assertLessEqual(max(im.size),MAX_SIDE)
                self.assertLessEqual(im.width*im.height,MAX_PIXELS)

    def test_truncated_jpeg_is_not_accepted_as_ready(self):
        raw=encoded((100,100))[:-100]
        with self.assertRaises(Exception):image_extension(raw)
        with self.assertRaises(Exception):prepare_image(raw)

    def test_middleware_repairs_history_without_mutating_it(self):
        raw=encoded((1050,8274))
        url='data:image/jpeg;base64,'+base64.b64encode(raw).decode()
        request={'messages':[{'role':'user','content':[{'type':'image_url','image_url':{'url':url}}]}]}
        self.assertIsNone(image_request(request,base_url='https://other.example'))
        result=image_request(request,base_url='https://api.deepseek.com')['request']
        self.assertEqual(request['messages'][0]['content'][0]['image_url']['url'],url)
        self.assertEqual(len(result['messages'][0]['content']),4)

    def test_invalid_inline_image_does_not_poison_next_request(self):
        request={'messages':[{'role':'user','content':[{'type':'image_url','image_url':{'url':'data:image/jpeg;base64,broken...'}}]}]}
        result=image_request(request,base_url='https://api.deepseek.com')['request']
        self.assertEqual(result['messages'][0]['content'][0]['type'],'text')
        self.assertIn('qq_image',result['messages'][0]['content'][0]['text'])

    def test_derived_paths_preserve_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'original.png';raw=encoded((20,20),'RGBA','PNG');path.write_bytes(raw)
            paths=vision_paths(path)
            self.assertEqual(path.read_bytes(),raw)
            self.assertTrue(all(Path(p).is_file() for p in paths))
            self.assertEqual(vision_paths(path),paths)
