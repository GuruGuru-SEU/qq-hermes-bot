"""DeliveryService responsibilities for the QQ adapter."""
import asyncio
import base64
import logging
from pathlib import Path
import secrets
import time
import math
import hashlib
from aiohttp import web
from gateway.platforms.base import SendResult
from .conversation import conversation
from .plaintext import plain_reply
from .outbound import send_result
LOG = logging.getLogger('qq-hermes')

class DeliveryService:
    async def send_final_ledgered(self,event,session_key,text_content,metadata,*,reply_to,is_ephemeral_response=False):
        from .live import LiveInbox
        chat = conversation(event.source.chat_id).key
        state = LiveInbox(self.store).state(chat, str(event.message_id))
        delivered = any(r['kind'] == 'result' and r['status'] == 'sent' for r in state['reports'])
        if not is_ephemeral_response and delivered and not state['open_tasks'] and not state['unclassified']:
            # All task results already have durable send receipts. A final model
            # recap (or [SILENT]) is not a new result and must not be echoed.
            return SendResult(success=True, raw_response={'delivery': 'already_reported'}), self
        from gateway.delivery_ledger import compute_obligation_id
        metadata=dict(metadata or {})
        if not is_ephemeral_response and not str(event.text or '').lstrip().startswith(('/',self.typed_command_prefix or '!')):
            mid=getattr(event,'ledger_message_id',None)
            if mid is None: mid=event.message_id
            metadata['qq_final_delivery']={
                'id':compute_obligation_id(session_key,str(mid or ''),text_content),
                'content':text_content}
        if not is_ephemeral_response and len(state['open_tasks']) == 1 and not state['unclassified']:
            reply_to = state['open_tasks'][0]['mid']
        result, delivery_adapter = await super().send_final_ledgered(event,session_key,text_content,metadata,
            reply_to=reply_to,is_ephemeral_response=is_ephemeral_response)
        if not is_ephemeral_response and result.success and len(state['open_tasks']) == 1 and not state['unclassified']:
            with self.store.db() as db:
                db.execute("UPDATE work_items SET state='completed',updated=? WHERE chat=? AND mid=?",
                           (time.time(), str(chat), state['open_tasks'][0]['mid']))
        return result, delivery_adapter

    async def work_report(self, request):
        if not secrets.compare_digest(request.headers.get('Authorization', '').removeprefix('Bearer '), self.token):
            raise web.HTTPUnauthorized()
        return web.json_response(await self.report_work(await request.json()))

    async def report_work(self, body):
        from .live import LiveInbox
        try:
            chat = conversation(body['chat_id']).key
            if not self.trusted(chat): raise PermissionError('Untrusted conversation')
            root_mid = str(body['root_mid'])
            source = str(body.get('message_id') or root_mid)
            target = str(body.get('task_id') or root_mid)
            kind = body['kind']
            text = body['text'].strip()
            outcome = body.get('outcome', 'completed')
            if kind not in ('acknowledgement', 'progress', 'clarification', 'result') or not text or len(text) > 12000:
                raise ValueError('Invalid report kind or text size')
            if outcome not in ('completed', 'waiting'): raise ValueError('Invalid outcome')
            report_source = target if kind == 'result' else source
            if kind == 'progress':
                report_source += ':' + hashlib.sha256(text.encode()).hexdigest()
            identity = hashlib.sha256(f'work:{chat}:{root_mid}:{target}:{report_source}:{kind}'.encode()).hexdigest()
            async with self.outbound_locks[chat]:
                inbox = LiveInbox(self.store)
                with self.store.db() as db:
                    if not inbox.active(db, chat, root_mid): raise ValueError('Foreground turn is no longer active')
                    row = db.execute('SELECT * FROM live_requests WHERE chat=? AND root_mid=? AND mid=?',
                                     (str(chat), root_mid, source)).fetchone()
                    item = db.execute('SELECT * FROM work_items WHERE chat=? AND mid=?', (str(chat), target)).fetchone()
                    if row is None or item is None: raise ValueError('Unknown source or task in this conversation')
                    if not db.execute('SELECT 1 FROM live_requests WHERE chat=? AND root_mid=? AND target=?',
                                      (str(chat), root_mid, target)).fetchone():
                        raise ValueError('Task is not associated with this turn')
                    # Reports do not race an already waiting follow-up or silently
                    # act on messages the agent has not classified.
                    if inbox.pending(db, chat, root_mid):
                        return {'success':False, 'delivery':'not_attempted', 'error':'New input is waiting; incorporate it before reporting.'}
                    if kind == 'result' and db.execute("SELECT 1 FROM live_requests WHERE chat=? AND root_mid=? AND relation=''", (str(chat), root_mid)).fetchone():
                        return {'success':False, 'delivery':'not_attempted', 'error':'Classify the received follow-ups before delivering a task result.'}
                    previous = db.execute('SELECT * FROM work_reports WHERE id=?', (identity,)).fetchone()
                    if previous and previous['text'] != text:
                        raise ValueError('This report already has a delivery identity; do not rewrite/repeat it')
                    if previous and previous['status'] == 'sent':
                        return {'success':True, 'delivery':'already_sent', 'id':identity}
                    if kind == 'progress' and db.execute("SELECT 1 FROM work_reports WHERE chat=? AND kind='progress' AND updated>?", (str(chat), time.time()-60)).fetchone():
                        return {'success':False,'delivery':'not_attempted','error':'Progress updates are limited to one per minute; finish useful work instead.'}
                    if not previous:
                        db.execute('INSERT INTO work_reports VALUES (?,?,?,?,?,?,?,?,?,?)',
                                   (identity,str(chat),root_mid,target,source,kind,text,outcome,'attempting',time.time()))
                # _send_text persists the send attempt before crossing the network;
                # unknown receipts never cause an automatic duplicate send.
                result = await self._send_text(chat, text, target if kind == 'result' else source,
                                              {'qq_final_delivery': {'id':identity,'content':text}})
                with self.store.db() as db:
                    db.execute('UPDATE work_reports SET status=?,updated=? WHERE id=?',
                               ('sent' if result.success else 'unknown',time.time(),identity))
                    if result.success and kind == 'result':
                        db.execute('UPDATE work_items SET state=?,updated=? WHERE chat=? AND mid=?',
                                   (outcome,time.time(),str(chat),target))
                return {**send_result(result, 'text'), 'id':identity,
                        'notice':'Report recorded. Do not repeat it. Continue remaining tasks; if all results were sent, return [SILENT].'}
        except (KeyError, ValueError, PermissionError) as exc:
            return {'success':False,'delivery':'not_attempted','error':str(exc)}

    async def send(self,chat_id,content,reply_to=None,metadata=None):
        group=conversation(chat_id).key
        if not self.trusted(group): return SendResult(success=False,error='Untrusted conversation')
        async with self.outbound_locks[group]:
            return await self._send_text(group,content,reply_to,metadata)

    async def _send_text(self,group,content,reply_to,metadata):
        final=(metadata or {}).get('qq_final_delivery')
        original=final['content'] if final else content
        identity=final['id'] if final else secrets.token_hex(24)
        ids=[]
        try:
            self.store.coalesce_outgoing_tail(identity)
            row=self.store.outgoing(identity)
            if row is None:
                text=plain_reply(original)
                # Exactly one QQ text send per reply. Do not segment or truncate.
                row=self.store.prepare_outgoing(identity,group,original,reply_to,[text])
            if row['chat']!=str(group) or row['content']!=original:
                raise ValueError('Delivery identity mismatch')
            for i,(part,receipt) in enumerate(zip(row['parts'],row['receipts'])):
                segments=[]
                if row['reply_to'] and i==0:
                    segments.append({'type':'reply','data':{'id':row['reply_to']}})
                segments.append({'type':'text','data':{'text':part}})
                if receipt['status']=='sent':
                    mid=receipt['message_id']
                elif receipt['status']!='pending':
                    return SendResult(success=False,error='Delivery confirmation unavailable; do not resend automatically.',
                        retryable=False,error_kind='unknown',
                        raw_response={'delivery':'unknown','sequence_id':identity,'message_ids':ids,'total':len(row['parts'])})
                else:
                    # Persist before awaiting QQ: a crash here is ambiguous, never blindly replayed.
                    self.store.outgoing_status(identity,i,'attempting')
                    result=await self.api('send_private_msg' if conversation(group).kind=='dm' else 'send_group_msg',
                        {**conversation(group).recipient,'message':segments})
                    mid=str(result['message_id'])
                    if not mid or mid=='None': raise ValueError('Missing send receipt')
                    self.store.outgoing_status(identity,i,'sent',mid)
                ids.append(mid)
                try:
                    self.archive(group).save({'message_id':mid,'time':receipt['updated'] if receipt['status']=='sent' else time.time(),
                        'user_id':self.cfg['bot_qq'],'sender':{'nickname':'Hermes'},'message':segments},'hermes')
                except Exception:
                    # An archive failure cannot turn an acknowledged send into an apparent send failure.
                    LOG.exception('outbound_archive_failed group=%s sequence=%s part=%s',group,identity,i)
            LOG.info('reply_delivery group=%s sequence=%s parts=%s final=%s',group,identity,len(ids),bool(final))
            return SendResult(success=True,message_id=ids[-1] if ids else None,
                continuation_message_ids=tuple(ids[:-1]),
                raw_response={'delivery':'sent','sequence_id':identity,'message_ids':ids})
        except Exception as exc:
            LOG.exception('send_failed group=%s sequence=%s acknowledged_parts=%s',group,identity,len(ids))
            return SendResult(success=False,error=type(exc).__name__,retryable=False,error_kind='unknown',
                raw_response={'delivery':'unknown','sequence_id':identity,'message_ids':ids})

    async def send_typing(self,chat_id,metadata=None): pass

    async def get_chat_info(self,chat_id):
        chat=conversation(chat_id)
        if not self.trusted(chat.key): raise PermissionError('Untrusted conversation')
        return {'name':chat.label,'type':chat.kind,'chat_id':chat.chat_id}

    def format_message(self,content):
        # Render exactly once in send(), retaining code/URL structure for boundary protection.
        return content

    def valid_output(self,path,group):
        p=Path(path).resolve()
        allowed=conversation(group).directory
        if not p.is_relative_to(allowed.resolve()) or not p.is_file(): raise PermissionError('File must belong to this conversation workspace')
        if p.stat().st_size>32*1024*1024: raise ValueError('Maximum file size is 32 MiB')
        return p

    async def send_image_file(self,chat_id,image_path,caption=None,**kwargs):
        group=conversation(chat_id).key
        if not self.trusted(group): return SendResult(success=False,error='Untrusted conversation')
        async with self.outbound_locks[group]:
            return await self._send_image_file(chat_id,image_path,caption,**kwargs)

    async def _send_image_file(self,chat_id,image_path,caption=None,**kwargs):
        try:
            group=conversation(chat_id).key
            if not self.trusted(group): raise PermissionError('Untrusted conversation')
            p=self.valid_output(image_path,group)
            from .media import image_extension
            raw=p.read_bytes(); image_extension(raw)
            segments=[{'type':'image','data':{'file':'base64://'+base64.b64encode(raw).decode()}}]
            if caption: segments.append({'type':'text','data':{'text':plain_reply(caption)}})
            result=await self.api('send_private_msg' if conversation(group).kind=='dm' else 'send_group_msg',{**conversation(group).recipient,'message':segments})
            return SendResult(success=True,message_id=str(result.get('message_id','')))
        except Exception as exc: return SendResult(success=False,error=type(exc).__name__)

    async def send_image(self,chat_id,image_url,caption=None,**kwargs):
        # Native local image output is supported; arbitrary remote URLs are not relayed blindly.
        return await self.send_image_file(chat_id,str(image_url).removeprefix('file://'),caption,**kwargs)

    async def send_document(self,chat_id,file_path,caption=None,**kwargs):
        group=conversation(chat_id).key
        if not self.trusted(group): return SendResult(success=False,error='Untrusted conversation')
        async with self.outbound_locks[group]:
            return await self._send_document(chat_id,file_path,caption,**kwargs)

    async def _send_document(self,chat_id,file_path,caption=None,**kwargs):
        token=None
        try:
            group=conversation(chat_id).key
            if not self.trusted(group): raise PermissionError('Untrusted conversation')
            p=self.valid_output(file_path,group)
            raw=p.read_bytes()
            token=secrets.token_urlsafe(32); self.blobs[token]=(raw,p.name,time.time()+120)
            url=f'http://qq-hermes-bot:8080/media/{token}'
            chat=conversation(group)
            result=await self.api('upload_private_file' if chat.kind=='dm' else 'upload_group_file',
                                  {**chat.recipient,'file':url,'name':p.name},timeout=60)
            LOG.info('group_file_upload_ok group=%s bytes=%s',group,len(raw))
            return SendResult(success=True,raw_response=result)
        except Exception as exc:
            LOG.warning('group_file_upload_issue group=%s kind=%s',chat_id,type(exc).__name__)
            ambiguous=isinstance(exc,(TimeoutError,ConnectionError))
            return SendResult(success=False,error=('Upload confirmation unavailable; delivery may have succeeded. Check this conversation for the file before retrying. ' if ambiguous else '')+type(exc).__name__,
                              error_kind='delivery_unknown' if ambiguous else 'upload_rejected',retryable=False)
        finally:
            if token: self.blobs.pop(token,None)

    async def serve_media(self,request):
        value=self.blobs.get(request.match_info['token'])
        if not value or value[2]<time.time(): raise web.HTTPNotFound()
        return web.Response(body=value[0],content_type='application/octet-stream')

    async def send_request(self,request):
        if not secrets.compare_digest(request.headers.get('Authorization','').removeprefix('Bearer '),self.token): raise web.HTTPUnauthorized()
        body=await request.json()
        group=conversation(body.get('chat_id',body.get('group'))).key
        if not self.trusted(group): raise web.HTTPForbidden()
        now=time.monotonic(); bucket=self.rates[group]
        while bucket and now-bucket[0]>60: bucket.popleft()
        if len(bucket)>=3:
            retry_after=max(1,math.ceil(60-(now-bucket[0])))
            LOG.info('group_send_rate_limited group=%s retry_after=%s',group,retry_after)
            return web.json_response({'success':False,'error':'Local conversation send rate limit (3/minute); this request was not sent to QQ. Earlier successful sends remain successful.',
                                      'error_kind':'rate_limited','retry_after':retry_after,'delivery':'not_attempted'},
                                     status=429,headers={'Retry-After':str(retry_after)})
        bucket.append(now)
        kind=body.get('kind','text')
        if kind=='text': result=await self.send(str(group),body['text'])
        elif kind=='image': result=await self.send_image_file(str(group),body['path'],body.get('text'))
        elif kind=='file': result=await self.send_document(str(group),body['path'],body.get('text'))
        else: raise web.HTTPBadRequest()
        return web.json_response(send_result(result,kind))
