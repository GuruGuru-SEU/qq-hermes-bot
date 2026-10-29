import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from gateway.config import PlatformConfig
from qqhermes.adapter import QQAdapter
import test_integration as fixtures


class InboxTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.AdapterTests.asyncSetUp
    asyncTearDown = fixtures.AdapterTests.asyncTearDown
    event = fixtures.AdapterTests.event
    async def test_restart_restores_only_never_started_requests_in_arrival_order(self):
        async def hold(event):
            event._gateway_accepted = True
        self.adapter.handle_message = hold
        self.adapter.ingest(self.event(1))
        self.adapter.ingest(self.event(2))
        self.adapter.ingest(self.event(3))
        await asyncio.sleep(.05)
        for task in list(self.adapter.tasks):
            task.cancel()
        await asyncio.gather(*self.adapter.tasks, return_exceptions=True)
        new = QQAdapter(PlatformConfig(enabled=True))
        new.receipt = AsyncMock()
        events = []
        async def handle(event):
            events.append(event)
            event._gateway_accepted = True
            await new.on_processing_complete(event, SimpleNamespace(value='success'))
        new.handle_message = handle
        try:
            new.restore_inbox()
            await asyncio.wait_for(new.queues[1].join(), 3)
            self.assertEqual([e.message_id for e in events], ['snowluma:2', 'snowluma:3'])
            new.ingest(self.event(2))
            self.assertTrue(new.queues[1].empty())
            # Restore uses original arrival context, not newer group messages.
            context = json.loads(events[0].channel_context.split('\n', 1)[1])
            self.assertNotIn('snowluma:3', [r['message_id'] for r in context['recent_new_messages']])
        finally:
            for task in list(new.tasks): task.cancel()
            await asyncio.gather(*new.tasks, return_exceptions=True)

    async def test_crash_reconciliation_does_not_replay_started_work_or_legacy_claims(self):
        self.adapter.store.enqueue(1, self.event(1), [])
        self.adapter.store.finish(1, 'snowluma:1', 'processing')
        self.adapter.store.claim(1, 'legacy')
        self.adapter.store.claim(1, 'legacy-queued')
        self.adapter.store.finish(1, 'legacy-queued', 'queued')
        self.assertEqual(self.adapter.store.recover_inbox(), [])
        with self.adapter.store.db() as db:
            states = [r['status'] for r in db.execute('SELECT status FROM triggers')]
        self.assertEqual(states, ['interrupted'] * 3)

    async def test_preparation_deadline_releases_turn_and_does_not_transcribe_incidental_audio(self):
        from qqhermes.preparation import prepare_attachments
        from unittest.mock import patch
        self.adapter.ingest(self.event(9, at=False, segments=[{'type': 'record', 'data': {}}]))
        self.adapter.transcribe = AsyncMock()
        async def slow(*args): await asyncio.sleep(30)
        self.adapter.images.cache_message = slow
        with patch('qqhermes.preparation.PREPARATION_SECONDS', .02):
            result = await prepare_attachments(self.adapter, self.adapter.archive(1), 1, 'm', ['m'])
        self.assertEqual(result['status'], 'deadline_reached')
        self.adapter.transcribe.assert_not_awaited()

    async def test_transient_transcription_has_real_cooldown(self):
        archive = self.adapter.archive(1)
        archive.set_transcription('v', status='retryable', error_kind='TimeoutError')
        self.adapter.api.reset_mock()
        await self.adapter.transcribe(archive, 'v')
        self.adapter.api.assert_not_awaited()
        with archive.connection() as db:
            db.execute("UPDATE transcriptions SET updated_at=0 WHERE message_id='v'")
        self.adapter.api.return_value = {'text': 'recovered'}
        await self.adapter.transcribe(archive, 'v')
        self.assertEqual(archive.transcription('v')['text'], 'recovered')
        self.adapter.api.assert_awaited_once()

    async def test_slow_image_for_one_group_does_not_block_another_group(self):
        from pathlib import Path
        from unittest.mock import patch
        from qqhermes.media import ImageCache
        from PIL import Image
        import io
        raw = io.BytesIO(); Image.new('RGB', (3, 3)).save(raw, format='PNG')
        started = asyncio.Event(); release = asyncio.Event()
        async def download(url):
            if 'slow' in url:
                started.set(); await release.wait()
            return raw.getvalue()
        for group in (1, 2):
            self.adapter.ingest(self.event(group, group=group, at=False, segments=[
                {'type': 'image', 'data': {'url': 'https://qpic.cn/' + ('slow' if group == 1 else 'fast')}}]))
        cache = ImageCache(self.adapter.api, self.adapter.trusted)
        with patch('qqhermes.media.download_image', side_effect=download) as downloader:
            slow = asyncio.create_task(cache.cache_message(self.adapter.archive(1), 1, 'snowluma:1'))
            duplicate = asyncio.create_task(cache.cache_message(self.adapter.archive(1), 1, 'snowluma:1'))
            await started.wait()
            await asyncio.wait_for(cache.cache_message(self.adapter.archive(2), 2, 'snowluma:2'), 2)
            self.assertEqual(self.adapter.archive(2).images('snowluma:2')[0]['status'], 'ready')
            release.set()
            await asyncio.gather(slow, duplicate)
            self.assertEqual(downloader.call_count, 2)
