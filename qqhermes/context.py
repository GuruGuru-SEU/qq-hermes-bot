"""Small arrival-snapshot context; archives remain complete and queryable."""
from collections import OrderedDict
import json
from pathlib import Path
import re
import time
from .mentions import incoming_mentions

# Generic pronouns and "评价" are not evidence that a request concerns an image.
IMAGE_REFERENCE = re.compile(r'这张|那张|图片|照片|截图|图里|图中|看图|张图|幅图|上图|下图|附图|图上|表情包|\b(?:images?|pictures?|photos?|screenshots?)\b', re.I)
RECENT_IMAGE_SECONDS = 120
RECENT_IMAGE_MESSAGES = 5
CONTEXT_SECONDS = 600


def attachments(archive,mid):
    result={}
    for kind,items in (('images',archive.images(mid)),('files',archive.files(mid))):
        if items:
            result[kind]=[{k:(v[:400] if isinstance(v,str) else v) for k,v in item.items()
                           if k in ('position','name','size','status','file_path')} for item in items[:3]]
            if len(items)>3:result[kind+'_omitted']=len(items)-3
    return result


class ContextWindow:
    def __init__(self):
        self.seen=OrderedDict()
        self.images=OrderedDict()

    def reset(self):
        self.seen.clear();self.images.clear()

    def expire(self):
        now=time.monotonic()
        for mapping in (self.seen,self.images):
            while mapping and (len(mapping)>200 or next(iter(mapping.values()))<now-300):
                mapping.popitem(last=False)

    def commit(self,ids,paths):
        for mapping,values in ((self.seen,ids),(self.images,paths)):
            for value in values:
                mapping.pop(value,None);mapping[value]=time.monotonic()
        self.expire()

    def select(self,archive,data,rows,text):
        self.expire()
        mid=str(data['message_id'])
        quoted=[str(s.get('data',{}).get('id','')) for s in data['message'] if s.get('type')=='reply'][:3]
        candidates={r['message_id']:r for r in rows}
        # Reply targets are often older than the ordinary context window.
        with archive.connection() as db:
            for target in quoted:
                row=db.execute('SELECT message_id,sender_id,timestamp,name,text FROM messages WHERE message_id=?',(target,)).fetchone()
                if row:candidates[target]=dict(zip(('message_id','sender_id','timestamp','name','text'),row))
        stamp=float(data.get('time',time.time()))
        selected=[r for r in rows if r['message_id'] not in self.seen and r['message_id']!=mid
                  and 0 <= stamp-float(r.get('timestamp',0)) <= CONTEXT_SECONDS][-12:]
        for target in quoted:
            if target in candidates and not any(r['message_id']==target for r in selected):
                selected.append(candidates[target])
        # Own attachments and exact reply targets take precedence, including old quoted images.
        media_ids=[mid]+[q for q in quoted if q in candidates]
        reference=bool(IMAGE_REFERENCE.search(text))
        if reference and not quoted and not archive.images(mid):
            eligible=[r for r in rows[-RECENT_IMAGE_MESSAGES:] if r['message_id']!=mid
                      and str(r['sender_id']) == str(data['user_id'])
                      and 0 <= stamp-float(r.get('timestamp',0)) <= RECENT_IMAGE_SECONDS
                      and archive.images(r['message_id'])]
            # With multiple candidates, ask or query history instead of guessing ownership.
            if len(eligible)==1:
                media_ids.append(eligible[0]['message_id'])
        return selected,list(dict.fromkeys(media_ids)),reference

    def render(self,archive,data,selected,media_ids,reference):
        mid=str(data['message_id']);rows=[];paths=[];sources=[]
        quoted={str(s.get('data',{}).get('id','')) for s in data['message'] if s.get('type')=='reply'}
        with archive.connection() as db:
            for target in media_ids:
                record=db.execute('SELECT sender_id,timestamp,name FROM messages WHERE message_id=?',(target,)).fetchone()
                for img in archive.images(target):
                    path=img.get('file_path')
                    if img['status']!='ready' or not path or not Path(path).is_file() or path in paths or len(paths)>=3:
                        continue
                    paths.append(path)
                    sources.append({'message_id':target,'position':img['position'],'path':path,
                        'sender_id':record[0] if record else data['user_id'],
                        'timestamp':record[1] if record else data.get('time'),
                        'sender_name':record[2] if record else '',
                        'relation':'current_message' if target==mid else 'explicit_reply_target' if target in quoted else 'recent_image_reference'})
        # Keep current request text in event.text only, not another copy in history context.
        for row in selected:
            item={k:row[k] for k in ('message_id','sender_id','name','time','timestamp') if k in row}
            item['text']=(archive.message_text(row['message_id']) or row.get('text',''))[:700]
            extra=attachments(archive,row['message_id'])
            # Incidental images in background chat are not attachments to this request.
            if 'images' in extra:
                extra['images']=[{k:v for k,v in x.items() if k!='file_path'} for x in extra['images']]
                extra['image_relation']='background_history; not selected for this request' if row['message_id'] not in media_ids else 'selected; see attached_image_sources'
            item.update(extra)
            rows.append(item)
        for row in rows:
            if len(row['text'])==700:row['text']+='… [query qq_history for full text]'
        size=lambda:len(json.dumps(rows,ensure_ascii=False,separators=(',',':')))
        while rows and size()>4000:rows.pop(0)
        ids=[r['message_id'] for r in rows]+[mid]
        payload={'current_message_id':mid,'recent_new_messages':rows,
                 'current_mention_user_ids':incoming_mentions(data),
                 'current_attachments':attachments(archive,mid),
                 'media_message_ids':media_ids,'attached_images':paths,'attached_image_sources':sources,
                 'current_sender_id':str(data['user_id']),'current_timestamp':data.get('time'),
                 'image_reference_unresolved':bool(reference and not sources),
                 'notice':'Bounded incremental context, not the full group history. Older or previously supplied messages may be omitted; use qq_history for missing messages, qq_image to retrieve archived images, or qq_file for files. Images are fetched before this turn; omitted images are not still loading. Only attached_image_sources identifies images selected for this request. Other image records and earlier-session pictures are historical background, not the current topic. Generic pronouns do not refer to an image automatically. If an image reference is unresolved, locate it with qq_history/qq_image or ask which image; never guess from an unrelated old picture.'}
        transcript=archive.transcription(mid)
        if transcript:
            payload['current_transcription']={'status':transcript['status'],'text':(transcript.get('text') or '')[:4000]}
            if len(transcript.get('text') or '')>4000:payload['current_transcription']['truncated']=True
        return json.dumps(payload,ensure_ascii=False,separators=(',',':')),paths,ids
