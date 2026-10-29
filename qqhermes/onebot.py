"""OneBotClient responsibilities for the QQ adapter."""
import asyncio
import json
import logging
import secrets
from aiohttp import web, WSMsgType
LOG = logging.getLogger('qq-hermes')

class OneBotClient:
    async def socket(self, request):
        supplied=request.headers.get('Authorization','').removeprefix('Bearer ')
        if not secrets.compare_digest(supplied,self.token): raise web.HTTPUnauthorized()
        if self.ws and not self.ws.closed: raise web.HTTPConflict(text='Already connected')
        ws=web.WebSocketResponse(heartbeat=30,max_msg_size=8*1024*1024)
        await ws.prepare(request)
        self.ws=ws
        LOG.info('onebot_connected')
        try:
            if not self.inbox_restored:
                self.restore_inbox()
                self.inbox_restored = True
            async for frame in ws:
                if frame.type!=WSMsgType.TEXT: continue
                try: data=json.loads(frame.data)
                except ValueError: continue
                echo=data.get('echo')
                if echo in self.pending:
                    fut=self.pending.pop(echo)
                    if not fut.done(): fut.set_result(data)
                    continue
                if data.get('post_type')=='message' and data.get('message_type') in ('group','private'):
                    try: self.ingest(self.gateway.message(data))
                    except Exception: LOG.exception('ingest_failed')
        finally:
            if self.ws is ws: self.ws=None
            for fut in self.pending.values():
                if not fut.done(): fut.set_exception(ConnectionError('OneBot disconnected'))
            self.pending.clear()
            LOG.warning('onebot_disconnected')
        return ws

    async def api(self, action, params, timeout=30):
        if not self.ws or self.ws.closed: raise ConnectionError('QQ gateway offline')
        echo=secrets.token_hex(12)
        fut=asyncio.get_running_loop().create_future()
        self.pending[echo]=fut
        try:
            await self.ws.send_json({'action':action,'params':self.gateway.params(action,params),'echo':echo})
            result=await asyncio.wait_for(fut,timeout)
            if result.get('status')!='ok' or result.get('retcode',0)!=0:
                raise RuntimeError(f'{action} rejected: {result.get("retcode")}')
            return self.gateway.response(action,result.get('data') or {})
        finally: self.pending.pop(echo,None)
