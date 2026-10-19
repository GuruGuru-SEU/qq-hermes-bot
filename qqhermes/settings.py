import json
import ipaddress
import os
from pathlib import Path


def settings():
    return json.loads(Path(os.environ.get('QQ_CONFIG', '/etc/qq/config.json')).read_text())


def root():
    return Path(os.environ.get('QQ_STATE', '/state'))


def fake_ip_networks(entries=None):
    """Explicit Mihomo DNS sentinels; never permit private LAN ranges."""
    if entries is None:
        entries = settings().get('fake_ip_ranges', [])
    if not isinstance(entries, list):
        raise ValueError('fake_ip_ranges must be a list of benchmark-range CIDRs')
    allowed = ipaddress.ip_network('198.18.0.0/15')
    result = []
    for entry in entries:
        network = ipaddress.ip_network(entry, strict=False)
        if network.version != 4 or not network.subnet_of(allowed):
            raise ValueError('fake_ip_ranges must be subnets of 198.18.0.0/15')
        result.append(network)
    return tuple(result)


def group_for_profile():
    from hermes_constants import get_hermes_home
    home = Path(get_hermes_home())
    cfg = settings()
    for group in cfg['trusted_groups']:
        if home.name == f'qq-{group}':
            return int(group)
    raise PermissionError('QQ tools require a trusted group profile')
