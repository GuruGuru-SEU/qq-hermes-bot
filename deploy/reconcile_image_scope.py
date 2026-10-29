"""Repair the observed latest-picture skill advice; preview unless --apply."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

OLD='用 read_image 读触发消息或最近图片的 file_path。'
NEW=('以本轮 attached_image_sources 的消息 ID、发送者、时间和引用关系确认目标图；'
     '不要把“最近图片”当作默认目标。当前未附带时，用 qq_history 定位用户明确所指的图片，'
     '再用 qq_image 获取并用 vision_analyze 查看；对象不明确时先问是哪张图。')


def reconcile(home,apply=False):
    changes=[]
    for path in sorted((Path(home)/'profiles').glob('qq-*/skills/**/price-guess/SKILL.md')):
        old=path.read_text();new=old.replace(OLD,NEW)
        if old==new:continue
        profile=next(p for p in path.parents if p.parent.name=='profiles')
        entry={'profile':profile.name,'path':str(path.relative_to(profile)),
               'before_sha256':hashlib.sha256(old.encode()).hexdigest(),
               'after_sha256':hashlib.sha256(new.encode()).hexdigest()}
        if apply:
            backup=profile/'backups'/('image-scope-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ'))
            backup.mkdir(parents=True,mode=0o700)
            target=backup/'SKILL.md';target.write_text(old);target.chmod(0o600)
            temp=path.with_suffix('.scope.tmp');temp.write_text(new);temp.chmod(path.stat().st_mode&0o777);temp.replace(path)
            entry['backup']=str(backup)
        changes.append(entry)
    return changes


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('home');parser.add_argument('--apply',action='store_true')
    args=parser.parse_args();print(json.dumps(reconcile(args.home,args.apply),indent=2))
