"""Copy archived history with SQLite backup, preserving cached paths and originals."""
import argparse
import json
from pathlib import Path
import shutil
import sqlite3

p=argparse.ArgumentParser();p.add_argument('source');p.add_argument('destination');a=p.parse_args()
src=Path(a.source);dest=Path(a.destination)
cfg=json.loads((src/'config/config.json').read_text())
for group in cfg['trusted_groups']:
    old=src/'state/groups'/str(group);new=dest/'state/groups'/str(group)
    new.mkdir(parents=True,exist_ok=True)
    origin=sqlite3.connect(f'file:{old / "history.sqlite3"}?mode=ro',uri=True)
    target=sqlite3.connect(new/'history.sqlite3')
    origin.backup(target)
    count=origin.execute('SELECT count(*) FROM messages').fetchone()[0]
    actual=target.execute('SELECT count(*) FROM messages').fetchone()[0]
    assert count==actual
    assert target.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    origin.close();target.close()
    for name in ('media','attachments'):
        if (old/name).exists(): shutil.copytree(old/name,new/name,dirs_exist_ok=True)
    profile=dest/'hermes/profiles'/f'qq-{group}'
    # Preserve detailed group memory as reference files and seed concise native memory.
    if (old/'memory').exists():
        shutil.copytree(old/'memory',new/'workspace/imported-memory',dirs_exist_ok=True)
        native=profile/'memories'/'MEMORY.md';native.parent.mkdir(parents=True,exist_ok=True)
        if not native.exists():
            content=(old/'memory/MEMORY.md').read_text() if (old/'memory/MEMORY.md').exists() else ''
            native.write_text(content[:1800]+'\nDetailed imported group memory is in '+str(Path('/state/groups')/str(group)/'workspace/imported-memory')+'.\n')
    if (old/'skills').exists():
        # Installed skills are symlinks into /state inside the old container.
        for item in (old/'skills').iterdir():
            if item.name.startswith('.'): continue
            resolved=item
            if item.is_symlink():
                link=Path(item.readlink())
                resolved=src/'state'/link.relative_to('/state') if link.is_absolute() and link.is_relative_to('/state') else item.resolve()
            if not resolved.exists():
                raise FileNotFoundError(f'Missing installed skill: {item.name}')
            output=new/'workspace/imported-skills'/item.name
            if resolved.is_dir():
                shutil.copytree(resolved,output,dirs_exist_ok=True)
                for skill in resolved.rglob('SKILL.md'):
                    native=profile/'skills'/skill.parent.name
                    if not native.exists(): shutil.copytree(skill.parent,native)
            else:
                output.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(resolved,output)
    print(json.dumps({'group':group,'messages':actual,'integrity':'ok'}))
