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

    def test_native_delivery_uses_plain_content_without_management_wrapper(self):
        from contextlib import ExitStack
        from cron import scheduler_delivery as delivery
        # Exercise Hermes's actual formatter; replace only transport and state writes.
        for chat_id in ('123', 'dm-42'):
            for wrapped in (False, True):
                job={'id':'test-job','name':'Drink water','deliver':'origin'}
                body='喝杯水，休息一下。'
                target={'platform':'qq_onebot','chat_id':chat_id}
                with ExitStack() as stack:
                    stack.enter_context(patch.dict('os.environ',{'_HERMES_CRON_EXTERNAL_WORKER':''}))
                    stack.enter_context(patch.object(delivery,'_resolve_delivery_targets',return_value=[target]))
                    stack.enter_context(patch.object(delivery._sched,'load_config',return_value={'cron':{'wrap_response':wrapped}}))
                    stack.enter_context(patch('gateway.config.load_gateway_config',return_value=SimpleNamespace()))
                    stack.enter_context(patch('gateway.media_policy.apply_media_policy_env'))
                    stack.enter_context(patch.object(delivery,'_prepare_target_delivery',return_value=SimpleNamespace(live_adapter_ready=True)))
                    send=stack.enter_context(patch.object(delivery,'_deliver_via_live_adapter',return_value=True))
                    stack.enter_context(patch.object(delivery,'_record_delivery_verification'))
                    self.assertIsNone(delivery._deliver_result(job,body,adapters={}))
                    sent=send.call_args.args[1]
                    if wrapped:
                        self.assertIn('Cronjob Response:',sent)
                    else:
                        self.assertEqual(sent,body)
                        self.assertNotIn('job_id:',sent)
                        self.assertNotIn('To stop or manage',sent)
