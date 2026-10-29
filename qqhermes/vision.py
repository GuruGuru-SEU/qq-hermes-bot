"""Provider-safe image copies; original QQ media remains untouched."""
import base64
from collections import OrderedDict
from copy import deepcopy
import hashlib
import io
import logging
import json
import re
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


def historical_image_context(request):
    """Drop old visual payloads from QQ requests only; never rewrite saved sessions."""
    def text_content(value):
        if isinstance(value,str):return value
        if isinstance(value,list):return '\n'.join(p.get('text','') for p in value if isinstance(p,dict))
        return ''
    messages=request.get('messages',[])
    current=next((i for i in range(len(messages)-1,-1,-1) if messages[i].get('role')=='user'),None)
    if current is None:return False
    current_text=text_content(messages[current].get('content'))
    if not current_text.startswith(('Untrusted QQ group context','Untrusted QQ dm context')) or '[New message]' not in current_text:
        return False  # Do not change standalone vision, compression or other auxiliary requests.
    changed=False
    def old_text(text):
        # Hermes stores attachment paths alongside text and may replay them on later turns.
        value=re.sub(r'\[Image attached at:[^\]\n]*\](?:\s*\[screenshot\])?',
                     '[Historical image omitted from this turn; retrieve its archived message_id only if explicitly relevant.]',text)
        if value.startswith(('Untrusted QQ group context','Untrusted QQ dm context')):
            head,sep,body=value.partition('\n')
            try:
                payload,end=json.JSONDecoder().raw_decode(body)
                if isinstance(payload,dict):
                    payload.pop('attached_images',None);payload.pop('attached_image_sources',None)
                    def prune(obj):
                        if isinstance(obj,dict):
                            for key in ('file_path','path','image_paths'):obj.pop(key,None)
                            for child in obj.values():prune(child)
                        elif isinstance(obj,list):
                            for child in obj:prune(child)
                    # Only image descriptors; leave historic file/tool paths alone.
                    prune(payload.get('current_attachments',{}).get('images',[]))
                    for row in payload.get('recent_new_messages',[]):prune(row.get('images',[]))
                    payload['image_scope']='historical context only; not an attachment to the current message'
                    value=head+sep+json.dumps(payload,ensure_ascii=False,separators=(',',':'))+body[end:]
            except (ValueError,TypeError,AttributeError):pass
        return value
    for message in messages[:current]:
        content=message.get('content')
        if isinstance(content,str) and message.get('role')=='user':
            cleaned=old_text(content)
            if cleaned!=content:message['content']=cleaned;changed=True
        elif isinstance(content,list):
            parts=[]
            for part in content:
                if part.get('type')=='image_url':
                    parts.append({'type':'text','text':'[Historical image omitted; use qq_history/qq_image if the current request explicitly refers to it.]'})
                    changed=True
                elif part.get('type')=='text' and message.get('role')=='user':
                    cleaned=old_text(part.get('text',''))
                    if cleaned!=part.get('text',''):changed=True
                    parts.append({**part,'text':cleaned})
                else:parts.append(part)
            message['content']=parts
    return changed


def image_request(request, base_url='', **kwargs):
    """Hermes llm_request middleware also handles images replayed from history."""
    if urlparse(base_url).hostname not in ('api.deepseek.com', 'api.deepseek.cn'):
        return
    result = deepcopy(request)
    changed = historical_image_context(result)
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
        return {'request':result, 'source':'qq-vision', 'reason':'Scope QQ image context to the current turn and validate images; preserve archives and sessions'}
