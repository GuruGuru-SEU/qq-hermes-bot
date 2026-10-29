"""Prepare literal reminder scripts for native Hermes cron; never schedule or send."""
import argparse
import json
from pathlib import Path
import uuid

from .conversation import conversation_for_profile


def prepare_reminder(text_file):
    from hermes_constants import get_hermes_home
    chat=conversation_for_profile()
    home=Path(get_hermes_home()).resolve()
    source=Path(text_file).expanduser().resolve()
    if not any(source.is_relative_to(base.resolve()) for base in (home,chat.directory/'workspace')):
        raise ValueError('Reminder text must be inside this conversation profile or workspace')
    with source.open('rb') as stream:
        raw=stream.read(16001)
    if len(raw)>16000:
        raise ValueError('Reminder text exceeds 16000 bytes')
    text=raw.decode('utf-8')
    if not text.strip() or len(text)>4000:
        raise ValueError('Reminder must contain 1–4000 characters')
    scripts=home/'scripts'
    scripts.mkdir(exist_ok=True)
    if scripts.resolve()!=scripts:
        raise ValueError('Reminder scripts directory must not be a symlink')
    name='qq-reminder-'+uuid.uuid4().hex+'.py'
    # repr stores arbitrary user text as data, without evaluating shell/Python syntax.
    with (scripts/name).open('x',encoding='utf-8') as stream:
        stream.write('import sys\nsys.stdout.write('+repr(text)+')\n')
    (scripts/name).chmod(0o600)
    return {'script':name,'no_agent':True,'deliver':'origin','failure_deliver':'local',
            'scheduled':False,'characters':len(text)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--text-file',required=True)
    args=parser.parse_args()
    try: result=prepare_reminder(args.text_file)
    except (OSError,ValueError,PermissionError) as exc: parser.exit(1,str(exc)+'\n')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__': main()
