"""Read-only archive CLI; storage/query API lives in history.py."""
import argparse
import json
import os
from pathlib import Path
import re
import sqlite3

from .history import query, statistics, export_result
from .settings import settings
from .conversation import conversation


def current_workspace():
    # Native profiles (including cron/delegates) own their conversation archive.
    try:
        from .conversation import conversation_for_profile
        return conversation_for_profile().directory/'workspace'
    except (ImportError, PermissionError, FileNotFoundError):
        pass
    # Compatibility for helpers launched with only a workspace cwd (no profile override).
    from .settings import root, settings
    from .conversation import Conversation
    cwd=Path.cwd().resolve()
    for directory in [cwd,*cwd.parents]:
        if directory.name!='workspace': continue
        for kind,bucket in (('group','groups'),('dm','users')):
            try: parts=directory.relative_to(root()/bucket).parts
            except ValueError: continue
            if not parts or not parts[0].isdigit(): continue
            chat=Conversation(kind,int(parts[0]))
            legacy_group=(kind=='group' and ((len(parts)==4 and parts[1]=='foreground'
                          and re.fullmatch(r'[a-f0-9]{16}',parts[2])) or
                          (len(parts)==6 and parts[1]=='background' and parts[3]=='runs'
                           and re.fullmatch(r'[a-f0-9]{16}',parts[2]) and parts[4].isdigit())))
            if chat.permitted(settings()) and (len(parts)==2 or legacy_group): return directory
    raise ValueError('Run helpers inside the current conversation workspace')


def current_archive():
    workspace = current_workspace()
    from .settings import root
    for kind in ('groups','users'):
        try:
            ident=workspace.relative_to(root()/kind).parts[0]
            return root()/kind/ident/'history.sqlite3'
        except ValueError: pass
    raise ValueError('No conversation archive for this workspace')


def main():
    parser = argparse.ArgumentParser(description="Query this QQ conversation's persisted messages; dates use Asia/Shanghai. Ranges include both endpoint days.")
    parser.add_argument("--date", help="YYYY-MM-DD or month.day")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--user", type=int)
    parser.add_argument("--contains")
    parser.add_argument("--message-id")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--order", choices=['asc', 'desc'], default='asc')
    parser.add_argument("--sample", type=int, help="Select up to 200 messages balanced across days; cannot combine with offset")
    parser.add_argument("--seed", default='0')
    parser.add_argument("--context", type=int, default=0, help="0..5 neighboring messages each side, plus recorded reply targets")
    parser.add_argument("--exclude-user", action='append', type=int, default=[])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--members", action='store_true', help="List archive-derived QQ IDs, names and counts")
    mode.add_argument("--stats", action='store_true', help="Filtered totals and daily counts")
    parser.add_argument("--name", help="Member nickname substring; may match multiple QQ IDs")
    parser.add_argument("--output", help="Export this bounded selection into a new workspace file")
    parser.add_argument("--format", choices=['jsonl', 'text'], default='jsonl')
    args = vars(parser.parse_args())
    try:
        members, stats = args.pop('members'), args.pop('stats')
        name, output, format = args.pop('name'), args.pop('output'), args.pop('format')
        if members or stats:
            if args['sample'] is not None or args['context'] or args['order'] != 'asc' or args['seed'] != '0':
                raise ValueError('Sampling, ordering and context apply only to message queries')
            for key in ('sample', 'context', 'order', 'seed'):
                args.pop(key)
            result = statistics(current_archive(), members=members, name=name, **args)
        else:
            if name is not None:
                raise ValueError('--name requires --members; use --user QQ_ID for messages')
            result = query(current_archive(), **args)
        archive=current_archive()
        result['user_id' if archive.parent.parent.name=='users' else 'group_id'] = int(archive.parent.name)
        if output:
            result = export_result(result, output, current_workspace(), format)
        print(json.dumps(result, ensure_ascii=False))
    except (ValueError, OSError, sqlite3.Error) as exc:
        parser.exit(1, "History query failed: " + str(exc) + "\n")

