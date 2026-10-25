"""Exercise native cron delivery with local-only satellite adapters."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from deploy.patch_hermes_cron import apply, PATCHES


class CronCompatibilityTests(unittest.TestCase):
    def test_patch_fails_before_any_partial_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name,(before,_) in PATCHES.items():
                p=root/name;p.parent.mkdir(parents=True,exist_ok=True)
                p.write_text(before+'\n    pass\n')
            (root/'cron/scheduler_provider.py').write_text('changed upstream\n')
            original=(root/'gateway/run.py').read_text()
            with self.assertRaises(RuntimeError):apply(root)
            self.assertEqual(original,(root/'gateway/run.py').read_text())

    def test_local_adapter_does_not_block_exact_routed_delivery(self):
        from gateway.run import _drain_restart_safe_cron_deliveries
        from gateway.config import Platform
        from gateway.profile_routing import ProfileRoute
        from cron.scheduler_preflight import SharedRouteAdapters
        from cron import scheduler
        # Plugin test setup registers its custom platform; dynamic enum also works directly.
        from gateway.platform_registry import platform_registry, PlatformEntry
        platform_registry.register(PlatformEntry(name="qq_onebot",label="QQ test",adapter_factory=lambda config: None,check_fn=lambda:True))
        qq=Platform('qq_onebot')
        primary=object();local=object();secondary=object()
        route=ProfileRoute(name='qq-dm-test',platform='qq_onebot',profile='qq-dm-test',chat_id='dm-123')
        for own,should_share in [({},True),({Platform.LOCAL:local},True),({qq:secondary},False),({Platform.LOCAL:local,qq:secondary},False)]:
            runner=SimpleNamespace(_profile_adapters={'qq-dm-test':own})
            with tempfile.TemporaryDirectory() as tmp, patch('gateway.run._handoff_watch_scopes',return_value=[('qq-dm-test',Path(tmp))]), patch('cron.scheduler_preflight._primary_profile_routes_for_current_home',return_value=[route]), patch.object(scheduler,'drain_delivery_queue') as drain:
                _drain_restart_safe_cron_deliveries({qq:primary},None,runner)
                transport=drain.call_args.args[0]
                if should_share:
                    self.assertIsInstance(transport,SharedRouteAdapters)
                    self.assertIs(transport.get(qq,{'chat_id':'dm-123'}),primary)
                    self.assertIsNone(transport.get(qq,{'chat_id':'dm-456'}))
                    self.assertIsNone(transport.get(qq,{'chat_id':'123'}))
                else:self.assertIs(transport,own)
