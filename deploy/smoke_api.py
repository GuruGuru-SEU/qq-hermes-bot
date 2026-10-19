"""Minimal provider checks; no QQ messages and no credentials in output."""
import base64,io,os,sys
sys.path.insert(0,'/opt/qq-hermes-bot')
import httpx
from PIL import Image,ImageDraw
from qqhermes.web import DeepSeekWeb
client=httpx.Client(timeout=90,headers={'Authorization':'Bearer '+os.environ['DEEPSEEK_API_KEY']})
response=client.get('https://api.deepseek.com/v1/models');response.raise_for_status()
print('Available models:',[x['id'] for x in response.json()['data']],flush=True)
picture=Image.new('RGB',(192,192),'white');draw=ImageDraw.Draw(picture);draw.rectangle((30,30,160,160),fill='red')
raw=io.BytesIO();picture.save(raw,format='PNG')
body={'model':'deepseek-flash','messages':[{'role':'user','content':[{'type':'text','text':'What color is the square? Reply with one English word.'},{'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(raw.getvalue()).decode()}}]}],'max_tokens':100,'thinking':{'type':'disabled'}}
r=client.post('https://api.deepseek.com/v1/chat/completions',json=body)
print('Vision HTTP:',r.status_code,flush=True)
r.raise_for_status();answer=r.json()['choices'][0]['message']['content'];print('Vision answer:',answer,flush=True)
assert 'red' in answer.lower()
web=DeepSeekWeb();result=web.search('Python programming official website',2)
print('Search success:',result.get('success'),'results:',len(result.get('data',{}).get('web',[])),flush=True)
assert result.get('success') and result['data']['web']
result=web.extract(['https://www.python.org/'])
print('Fetch content length:',len(result['data'][0]['content']),flush=True)
assert len(result['data'][0]['content'])>100
print('PROVIDER CHECKS PASSED',flush=True)
