"""Read-only preflight; run inside the bot container before an idle-only update.

Exit 0 = idle, 1 = busy, 2 = cannot establish status. Never stops anything.
Check again immediately before stopping; this is a snapshot, not an ingress lock.
"""
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from urllib.request import urlopen


def snapshot():
    from gateway.control_socket import query_gateway_control
    work = query_gateway_control(Path(os.environ.get('HERMES_HOME', '/opt/data')), 'active-work')
    if not isinstance(work, dict) or 'active_agents' not in work or 'active_work' not in work:
        raise RuntimeError('Native gateway did not provide a live work snapshot')
    with urlopen('http://127.0.0.1:8080/health', timeout=5) as response:
        health = json.load(response)
    if not health.get('ready'):
        raise RuntimeError('QQ transport is not ready')
    pid = int(work['pid'])
    # Ignore legacy stale claims from previous gateway lifetimes, but include
    # media preparation and status/stop replies not yet visible to native Hermes.
    ticks = float(Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19])
    started = time.time() - float(Path('/proc/uptime').read_text().split()[0]) + ticks / os.sysconf('SC_CLK_TCK')
    path = Path(os.environ.get('QQ_STATE', '/state')) / 'transport.sqlite3'
    with sqlite3.connect(f'{path.as_uri()}?mode=ro', uri=True) as db:
        pending = db.execute("SELECT COUNT(*) FROM triggers WHERE status IN ('accepted','queued','processing') AND updated>=?", (started,)).fetchone()[0]
    # Older bridge images do not expose the process registry in /health.
    # Direct tool workers under the gateway must also finish before stopping.
    children = Path(f'/proc/{pid}/task/{pid}/children').read_text().split()
    counts = {
        'native_agents': work['active_agents'], 'native_work': len(work['active_work']),
        'queued': sum(health.get('queued', {}).values()), 'pending_turns': pending,
        'foreground': health.get('foreground_requests', health.get('active_sessions', 0)),
        'running_processes': health.get('running_processes', 0),
        'pending_watchers': health.get('pending_process_watchers', 0),
        'gateway_child_processes': len(children),
    }
    return {'idle': not any(counts.values()), **counts}


def main():
    try:
        result = snapshot()
    except Exception as exc:
        print(json.dumps({'idle': False, 'status': 'unknown', 'error': type(exc).__name__}))
        return 2
    print(json.dumps(result))
    return 0 if result['idle'] else 1


if __name__ == '__main__':
    sys.exit(main())
