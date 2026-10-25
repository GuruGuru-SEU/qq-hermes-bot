"""Repair known stale lessons; dry-run by default and apply with gateway stopped."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

MARKER = '<!-- qq-quality-v1 -->'
QUALITY_NOTE = '''
Current capability facts come from the deployed runtime and tools, not old incidents.
Tone preferences affect wording, never factual accuracy or completing the request.
Remember explicit durable preferences with source/date; keep one-off tasks and guesses
out of permanent memory. Recheck operational observations after upgrades.
'''


def repair_memory(text):
    parts=text.split('§')
    for i,part in enumerate(parts):
        if '工作区' in part and 'HERMES_WRITE_SAFE_ROOT(/opt/data)' in part and '直接写会被拒' in part:
            part=re.sub(r'本群工作区[^。]*直接写会被拒；[^。]*。',
                '运行时已为可信会话工作区配置文件写入权限；旧的“工作区不可写”故障已修复，遇到新错误以当前工具结果为准。',part,count=1)
        if '图片消息常先到为 pending 状态，未就绪时别猜内容。' in part:
            part=part.replace('图片消息常先到为 pending 状态，未就绪时别猜内容。','')
        if '图片附件先到为 pending' in part and '等下一轮再读' in part:
            part='\n图片由桥接在本轮前取回；上下文缺图时先用 qq_history 定位消息，再用 qq_image 取图并查看。不要把未附带的图片说成“仍在加载”，不要要求用户无故重发。\n'
        parts[i]=part
    new='§'.join(parts)
    if MARKER not in new:new=new.rstrip()+'\n\n'+MARKER+'\n'+QUALITY_NOTE
    return new


def repair_skill(text):
    text=text.replace('that is the authoritative answer.', 'that is evidence of display-name history, not necessarily the preferred form of address; check saved explicit preferences.')
    text=text.replace('Run '+chr(96)+'web_search'+chr(96)+' with', 'First inspect nearby context and plausible wordplay; when external verification is needed, run '+chr(96)+'web_search'+chr(96)+' with')
    text=text.replace('BEFORE answering. This group\'s slang is deliberately obfuscated and the classical/literal meaning is usually the wrong answer.', 'without assuming a literal or encoded meaning in advance. Keep a guess short and explicitly tentative.')
    text=text.replace('concede immediately with the correct number when called out.', 'verify the correction and its source before changing the number; a user assertion is not automatically verified.')
    return text


def reconcile(home,apply=False):
    home=Path(home);changes=[]
    for profile in sorted((home/'profiles').glob('qq-*')):
        paths=[profile/'memories/MEMORY.md',profile/'SOUL.md']
        paths+=list((profile/'skills').glob('**/qq-group-chat-replies/SKILL.md'))
        for path in paths:
            if not path.exists():continue
            old=path.read_text()
            if path.name=='MEMORY.md':new=repair_memory(old)
            elif path.name=='SOUL.md':new=old.replace('usually 1-3 short sentences.','briefly for casual chat; provide sufficient detail for analysis and deliverables.')
            else:new=repair_skill(old)
            if old==new:continue
            entry={'profile':profile.name,'path':str(path.relative_to(profile)),
                   'before_sha256':hashlib.sha256(old.encode()).hexdigest(),'after_sha256':hashlib.sha256(new.encode()).hexdigest()}
            if apply:
                backup=profile/'backups'/('quality-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ'))
                backup.mkdir(parents=True,mode=0o700)
                b=backup/path.name;b.write_text(old);b.chmod(0o600)
                record=backup/'change.json';record.write_text(json.dumps(entry,indent=2));record.chmod(0o600)
                temp=path.with_suffix('.quality.tmp');temp.write_text(new);temp.chmod(path.stat().st_mode & 0o777);temp.replace(path)
                entry['backup']=str(backup)
            changes.append(entry)
    return changes


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('home');p.add_argument('--apply',action='store_true')
    args=p.parse_args();print(json.dumps(reconcile(args.home,args.apply),indent=2))
