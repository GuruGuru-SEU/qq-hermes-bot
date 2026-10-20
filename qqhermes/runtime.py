"""Process-wide filesystem permissions for the trusted group deployment."""
import os
from pathlib import Path
from .settings import root, settings


def safe_write_roots():
    # Preserve an operator's existing roots and Hermes's credential/state denylist.
    # Add workspaces only, not /state (which also contains archives and the ledger).
    paths=list(filter(None,os.environ.get('HERMES_WRITE_SAFE_ROOT','').split(os.pathsep)))
    paths.append(os.environ.get('HERMES_HOME','/opt/data'))
    for group in settings()['trusted_groups']:
        paths.append(str(root()/'groups'/str(int(group))/'workspace'))
    return os.pathsep.join(dict.fromkeys(str(Path(p).expanduser().resolve()) for p in paths))


if __name__=='__main__':
    print(safe_write_roots())
