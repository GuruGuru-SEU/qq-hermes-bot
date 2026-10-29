"""Deployment facts and narrowly scoped migration of obsolete operational memory."""
from datetime import datetime, timezone
import sys
import json
from pathlib import Path
import shutil


def collect():
    fonts = Path('/usr/share/fonts/opentype/noto')
    return {
        'verified_at': datetime.now(timezone.utc).isoformat(),
        'uv': shutil.which('uv'),
        'chromium': shutil.which('chromium'),
        'chinese_fonts': [str(p) for p in sorted(fonts.glob('*CJK*'))],
        'python': sys.version.split()[0],
        'notice': 'Deployment observations, not permanent preferences. Inspect the workspace before reusing an old venv.',
    }


def migrate_observations(profile):
    """Move only the known mixed operational paragraph; leave preferences intact."""
    profile = Path(profile)
    memory = profile / 'memories/MEMORY.md'
    if not memory.exists():
        return False
    original = memory.read_text()
    lines = original.splitlines(keepends=True)
    moved = [line for line in lines if line.startswith('运行时已为可信会话工作区配置文件写入权限；')
             and '系统无 sqlite3 CLI、无中文字体（工作区有 NotoSansSC.otf）。' in line]
    if not moved:
        return False
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    backup = profile / 'backups' / ('runtime-observations-' + stamp)
    backup.mkdir(parents=True, mode=0o700)
    shutil.copy2(memory, backup / 'MEMORY.md')
    notes = profile / 'runtime-observations.md'
    if notes.exists():
        shutil.copy2(notes, backup / notes.name)
    with notes.open('a') as out:
        out.write('\nHistorical operational notes moved from memory at ' + stamp +
                  '. These may be obsolete; verify before use:\n' + ''.join(moved))
    notes.chmod(0o600)
    temporary = memory.with_suffix('.runtime.tmp')
    temporary.write_text(''.join(line for line in lines if line not in moved))
    temporary.chmod(memory.stat().st_mode & 0o777)
    temporary.replace(memory)
    return True


def write_capabilities(home):
    path = Path(home) / 'runtime-capabilities.json'
    path.write_text(json.dumps(collect(), ensure_ascii=False, indent=2))
