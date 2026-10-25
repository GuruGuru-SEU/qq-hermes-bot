"""Official DeepSeek retrieval APIs as a native Hermes web backend."""
import ipaddress
from datetime import datetime,timezone
from urllib.parse import urlsplit
from agent.web_search_provider import WebSearchProvider
from gateway.platforms._shared import get_scoped_secret


class DeepSeekWeb(WebSearchProvider):
    @property
    def name(self): return 'deepseek-api'
    @property
    def display_name(self): return 'DeepSeek Search / Fetch API'
    def is_available(self): return bool(get_scoped_secret('DEEPSEEK_API_KEY',''))
    def supports_search(self): return True
    def supports_extract(self): return True

    def request(self,operation,body):
        import httpx
        with httpx.Client(timeout=60,follow_redirects=False) as client:
            r=client.post('https://api.deepseek.com/v1/tools/'+operation,
                headers={'Authorization':'Bearer '+get_scoped_secret('DEEPSEEK_API_KEY')},json=body)
            r.raise_for_status();return r.json()

    def search(self,query,limit=5):
        try:
            data=self.request('search',{'query':query,'count':max(1,min(int(limit),20)),'need_content':False})
            rows=[]
            for i,v in enumerate(data.get('results',[])):
                if not v.get('url'):continue
                row={'title':v.get('title',''),'url':v['url'],'description':v.get('snippet',''),
                     'position':i+1,'source_domain':urlsplit(v['url']).hostname,'evidence_type':'search_snippet'}
                for key in ('published_at','published_date','publish_date','date','updated_at','source'):
                    if v.get(key) is not None:row[key]=v[key]
                rows.append(row)
            return {'success':True,'data':{'web':rows},'query':query,
                    'retrieved_at':datetime.now(timezone.utc).isoformat(),
                    'notice':'Search snippets are leads, not verified page contents. Fetch central sources, check event/publication dates and scope; retrieval time is not publication time.'}
        except Exception as exc: return {'success':False,'error':'DeepSeek search failed: '+type(exc).__name__}

    def extract(self,urls,**kwargs):
        if isinstance(urls,str): urls=[urls]
        rows=[]
        for url in urls[:10]:
            try:
                parsed=urlsplit(url)
                if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username: raise ValueError('Invalid URL')
                try: address=ipaddress.ip_address(parsed.hostname)
                except ValueError: address=None
                if address and not address.is_global: raise ValueError('Private URL')
                data=self.request('fetch',{'url':url});content=data['content']
                rows.append({'url':url,'title':data.get('title',''),'content':content,'raw_content':content,'evidence_type':'fetched_page','retrieved_at':datetime.now(timezone.utc).isoformat(),
                             **{k:data[k] for k in ('published_at','published_date','date','updated_at') if k in data}})
            except Exception as exc: rows.append({'url':url,'title':'','content':'','raw_content':'','error':type(exc).__name__})
        return rows
