"""Provider-safe image copies; original QQ media remains untouched."""
import base64
from collections import OrderedDict
from copy import deepcopy
import hashlib
import io
import logging
import math
from pathlib import Path
import threading
import warnings
from urllib.parse import urlparse

from PIL import Image, ImageOps

MAX_BYTES = 16 * 1024 * 1024
MAX_SIDE = 4096
MAX_PIXELS = 4_000_000
_CACHE = OrderedDict()
_LOCK = threading.Lock()
LOG = logging.getLogger('qq-hermes')


def prepare_image(raw):
    """Fully decode, orient, flatten and bound images. Tall pages become <=4 tiles."""
    if len(raw) > MAX_BYTES:
        raise ValueError('Image exceeds 16 MiB')
    digest = hashlib.sha256(raw).hexdigest()
    with _LOCK:
        if digest in _CACHE:
            _CACHE.move_to_end(digest)
            return _CACHE[digest]
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(raw)) as source:
            if source.width * source.height > 40_000_000:
                raise ValueError('Image exceeds pixel budget')
            source.load()  # verify() alone does not decode JPEG pixels.
            image = ImageOps.exif_transpose(source).convert('RGBA')
            rgb = Image.new('RGB', image.size, 'white')
            rgb.paste(image, mask=image.getchannel('A'))
    width, height = rgb.size
    # Bound long screenshots without turning all their text into a tiny thumbnail.
    count = min(4, math.ceil(height / min(MAX_SIDE, max(width * 3, 1)))) if height > width * 3 else 1
    result = []
    for i in range(count):
        tile = rgb.crop((0, height * i // count, width, height * (i + 1) // count))
        scale = min(1, MAX_SIDE / tile.width, MAX_SIDE / tile.height,
                    math.sqrt(MAX_PIXELS / (tile.width * tile.height)))
        if scale < 1:
            tile = tile.resize((max(1, int(tile.width * scale)), max(1, int(tile.height * scale))), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        tile.save(output, format='JPEG', quality=92)
        result.append(output.getvalue())
    value = tuple(result)
    with _LOCK:
        _CACHE[digest] = value
        while len(_CACHE) > 8:
            _CACHE.popitem(last=False)
    return value


def vision_paths(path):
    path = Path(path)
    directory = path.parent / 'vision'
    directory.mkdir(mode=0o700, exist_ok=True)
    paths = []
    for raw in prepare_image(path.read_bytes()):
        target = directory / (hashlib.sha256(raw).hexdigest() + '.jpg')
        if not target.exists():
            # Atomic even when two tools request the same attachment concurrently.
            import tempfile
            with tempfile.NamedTemporaryFile(dir=directory, delete=False) as out:
                out.write(raw)
                temporary = Path(out.name)
            temporary.replace(target)
        paths.append(str(target))
    return paths


def image_request(request, base_url='', **kwargs):
    """Hermes llm_request middleware also handles images replayed from history."""
    if urlparse(base_url).hostname not in ('api.deepseek.com', 'api.deepseek.cn'):
        return
    changed = False
    result = deepcopy(request)
    for message in result.get('messages', []):
        if not isinstance(message.get('content'), list):
            continue
        parts = []
        for part in message['content']:
            url = part.get('image_url', {}).get('url', '')
            if part.get('type') != 'image_url' or not url.startswith('data:'):
                parts.append(part)
                continue
            changed = True
            try:
                if len(url) > MAX_BYTES * 4 // 3 + 128:
                    raise ValueError('Image exceeds byte budget')
                raw = base64.b64decode(url.split(',', 1)[1], validate=True)
                tiles = prepare_image(raw)
                if len(tiles) > 1:
                    parts.append({'type':'text','text':f'The following {len(tiles)} images are consecutive top-to-bottom sections of one tall image.'})
                for tile in tiles:
                    parts.append({'type':'image_url','image_url':{'url':'data:image/jpeg;base64,' + base64.b64encode(tile).decode('ascii')}})
            except Exception as exc:
                LOG.warning('vision_image_rejected kind=%s', type(exc).__name__)
                parts.append({'type':'text','text':'[Image unavailable: bytes could not be decoded safely. Use qq_image with its archived message_id to retrieve a fresh copy; do not claim to see it or say it is still loading.]'})
        message['content'] = parts
    if changed:
        return {'request':result, 'source':'qq-vision', 'reason':'Bound and validate DeepSeek inline images; preserve archive originals'}
