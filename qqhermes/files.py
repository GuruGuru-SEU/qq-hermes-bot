"""Bounded download of a recorded attachment, using a gateway-issued public URL."""
import hashlib
import ipaddress
import os
from pathlib import Path
import re
import secrets
from aiohttp import ClientSession, ClientTimeout, TCPConnector
from yarl import URL
from .media import PublicResolver
from .conversation import conversation


def checked_url(value):
    url=URL(value)
    if url.scheme not in ('http','https') or not url.host or url.user or url.password or url.port not in (80,443):
        raise ValueError('Invalid file URL')
    try: address=ipaddress.ip_address(url.host)
    except ValueError: address=None
    if address and not address.is_global: raise ValueError('Private endpoint')
    return url


async def fetch_attachment(api,archive,group,mid,position):
    with archive.connection() as db:
        row=db.execute('SELECT file_id,name,size,path FROM files WHERE message_id=? AND position=?',(mid,position)).fetchone()
    if not row: raise ValueError('No recorded attachment in this group')
    fid,name,size,cached=row
    directory=archive.path.parent/'attachments';directory.mkdir(parents=True,exist_ok=True)
    if cached and Path(cached).resolve().is_relative_to(directory.resolve()) and Path(cached).is_file():
        return {'path':cached,'name':name,'size':Path(cached).stat().st_size}
    maximum=32*1024*1024
    if size and size>maximum: raise ValueError('File exceeds 32 MiB')
    if sum(p.stat().st_size for p in directory.iterdir() if p.is_file())+maximum>512*1024*1024: raise ValueError('Group attachment cache full')
    chat=conversation(group)
    params={**chat.recipient,'file_id':fid}
    if chat.kind=='dm':
        params['file_hash']=archive.segments(mid)[position].get('data',{}).get('file_hash','')
    response=await api('get_private_file_url' if chat.kind=='dm' else 'get_group_file_url',params)
    url=response['url'];tmp=directory/('.'+secrets.token_hex(12)+'.part')
    try:
        async with ClientSession(timeout=ClientTimeout(total=60),trust_env=False,connector=TCPConnector(resolver=PublicResolver())) as client:
            for _ in range(4):
                async with client.get(checked_url(url),allow_redirects=False) as r:
                    if r.status in (301,302,303,307,308): url=str(r.url.join(URL(r.headers['Location'])));continue
                    r.raise_for_status()
                    digest=hashlib.sha256();size=0
                    with tmp.open('xb') as out:
                        os.chmod(tmp,0o600)
                        async for chunk in r.content.iter_chunked(65536):
                            size+=len(chunk)
                            if size>maximum: raise ValueError('File exceeds 32 MiB')
                            out.write(chunk);digest.update(chunk)
                    break
            else: raise ValueError('Too many redirects')
        safe=re.sub(r'[^\w.() -]','_',name)[:100].strip(' .') or 'attachment'
        path=directory/(digest.hexdigest()[:20]+'-'+safe);tmp.replace(path)
        with archive.connection() as db:
            db.execute("UPDATE files SET path=?,status='ready' WHERE message_id=? AND position=?",(str(path),mid,position))
        return {'path':str(path),'name':name,'size':size}
    finally: tmp.unlink(missing_ok=True)
