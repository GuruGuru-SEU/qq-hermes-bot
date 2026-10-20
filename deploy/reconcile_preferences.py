"""Repair the observed learned lessons, retaining private dated backups.

Run against a stopped deployment. Memories, sessions and other skills stay intact.
"""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path

SEARCH = ('4. Verify archive queries. Put all qq_history parameters inside the deferred call arguments object, '
          'including an explicit action. Check applied_filters in the result. Malformed calls are rejected; '
          'correct the invocation instead of inferring a history-search limitation. Empty results mean no '
          'matching archived messages, not proof that nobody ever said it.')
STYLE = ('5. Reply in 1–3 short plain-text sentences, no Markdown. Follow current saved group and '
         'requester preferences. Do not add stock joke endings or closings that members have rejected.')
PRECEDENCE = ('\n\n## Current preferences take precedence\nThis skill records learned suggestions. Current explicit '
              'requests and current saved group/member preferences take precedence. Reconcile conflicts in the '
              'skill; never change an explicit preference merely to fit an older lesson.\n')


def reconcile(home):
    changes=[]
    for path in sorted(Path(home).glob('profiles/qq-*/skills/**/qq-group-chat-replies/SKILL.md')):
        old=path.read_text();lines=old.splitlines(keepends=True)
        for i,line in enumerate(lines):
            if line.startswith('4. Verify archive queries.') and 'may come back with an unfiltered page' in line:
                lines[i]=SEARCH+'\n'
            if line.startswith('5. Reply in 1–3 short plain-text sentences') and '（不是）' in line:
                lines[i]=STYLE+'\n'
        new=''.join(lines)
        if '## Current preferences take precedence' not in new:new+=PRECEDENCE
        if new==old:continue
        profile=next(p for p in path.parents if p.name.startswith('qq-') and p.parent.name=='profiles')
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
        backup=profile/'backups'/('qq-reliability-'+stamp);backup.mkdir(parents=True,mode=0o700)
        (backup/'SKILL.md').write_text(old)
        record={'path':str(path.relative_to(profile)),
                'before_sha256':hashlib.sha256(old.encode()).hexdigest(),
                'after_sha256':hashlib.sha256(new.encode()).hexdigest()}
        (backup/'change.json').write_text(json.dumps(record,indent=2))
        temporary=path.with_suffix('.tmp');temporary.write_text(new);temporary.replace(path)
        changes.append({'profile':profile.name,'skill':path.parent.name,'backup':str(backup)})
    return changes


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('hermes_home')
    print(json.dumps(reconcile(parser.parse_args().hermes_home),indent=2))
