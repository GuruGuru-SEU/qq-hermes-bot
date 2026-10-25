"""Narrow v0.22.5 compatibility fix for routed profiles with LOCAL adapters.

A completed cron creates a LocalSessionAdapter. Treating that as a messaging
connector disables SharedRouteAdapters and breaks subsequent QQ delivery. Keep
native exact-route authorization; any real secondary connector still wins.
Remove this patch when the upstream pinned release handles local-only maps.
"""
from pathlib import Path

PATCHES = {
    'gateway/run.py': (
        'if profile_name is not None and not profile_adapters and adapters:',
        'if profile_name is not None and not any(getattr(p, "value", p) != "local" for p in profile_adapters) and adapters:',
    ),
    'cron/scheduler_provider.py': (
        'if not tick_adapters and adapters:',
        'if not any(getattr(p, "value", p) != "local" for p in tick_adapters) and adapters:',
    ),
}


def apply(root):
    writes=[]
    for relative,(before,after) in PATCHES.items():
        path=Path(root)/relative
        source=path.read_text()
        if source.count(before)!=1:
            raise RuntimeError(f'Pinned Hermes cron patch no longer matches {relative}')
        writes.append((path,source.replace(before,after)))
    # Validate both anchors before changing either file.
    for path,source in writes:
        compile(source,str(path),'exec')
    for path,source in writes:path.write_text(source)


if __name__=='__main__':
    apply('/opt/hermes')
    print('Applied v0.22.5 local-only cron routing compatibility fix')
