import json
import os
from pathlib import Path


def settings():
    return json.loads(Path(os.environ.get('QQ_CONFIG', '/etc/qq/config.json')).read_text())


def root():
    return Path(os.environ.get('QQ_STATE', '/state'))


def group_for_profile():
    from hermes_constants import get_hermes_home
    home = Path(get_hermes_home())
    cfg = settings()
    for group in cfg['trusted_groups']:
        if home.name == f'qq-{group}':
            return int(group)
    raise PermissionError('QQ tools require a trusted group profile')
