"""Small arrival-snapshot context; archives remain complete and queryable."""
from collections import OrderedDict
import json
from pathlib import Path
import re
import time

IMAGE_REFERENCE = re.compile(r'图|照片|截图|画面|上面|刚才|这个|这张|猜价|评价|点评|运动记录|image|picture|photo|screenshot|attached|look at|what.*this',re.I)


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
        selected=[r for r in rows if r['message_id'] not in self.seen and r['message_id']!=mid][-12:]
        for target in quoted:
            if target in candidates and not any(r['message_id']==target for r in selected):selected.append(candidates[target])
        media_ids=[mid]+[q for q in quoted if q in candidates]
        reference=bool(quoted or IMAGE_REFERENCE.search(text))
        # Inspect the bounded arrival snapshot, regardless of sender. A newly seen
        # image may be the subject of an implicit question ("评价雪狼的运动记录").
        for row in reversed(rows):
            target=row['message_id']
            if target==mid or not archive.images(target):continue
            if reference or target not in self.seen:
                media_ids.append(target)
            break
        return selected,list(dict.fromkeys(media_ids)),reference

    def render(self,archive,data,selected,media_ids,reference):
        mid=str(data['message_id']);rows=[];paths=[]
        for target in media_ids:
            for img in archive.images(target):
                path=img.get('file_path')
                if img['status']=='ready' and path and Path(path).is_file():
                    if target==mid or reference or target not in self.seen:paths.append(path)
        paths=list(dict.fromkeys(paths))[:3]
        # Keep current request text in event.text only, not another copy in history context.
        for row in selected:
            item={k:row[k] for k in ('message_id','sender_id','name','time') if k in row}
            item['text']=(archive.message_text(row['message_id']) or row.get('text',''))[:700]
            item.update(attachments(archive,row['message_id']))
            rows.append(item)
        for row in rows:
            if len(row['text'])==700:row['text']+='… [query qq_history for full text]'
        size=lambda:len(json.dumps(rows,ensure_ascii=False,separators=(',',':')))
        while rows and size()>4000:rows.pop(0)
        ids=[r['message_id'] for r in rows]+[mid]
        payload={'current_message_id':mid,'recent_new_messages':rows,
                 'current_attachments':attachments(archive,mid),
                 'media_message_ids':media_ids,'attached_images':paths,
                 'notice':'Bounded incremental context, not the full group history. Older or previously supplied messages may be omitted; use qq_history for missing messages, qq_image to retrieve archived images, or qq_file for files. Images are fetched before this turn; omitted images are not still loading.'}
        transcript=archive.transcription(mid)
        if transcript:
            payload['current_transcription']={'status':transcript['status'],'text':(transcript.get('text') or '')[:4000]}
            if len(transcript.get('text') or '')>4000:payload['current_transcription']['truncated']=True
        return json.dumps(payload,ensure_ascii=False,separators=(',',':')),paths,ids
