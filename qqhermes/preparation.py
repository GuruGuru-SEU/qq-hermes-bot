"""A shared deadline for relevant media; incidental chat audio never blocks a turn."""
import asyncio
from . import activity

PREPARATION_SECONDS = 35


async def prepare_attachments(adapter, archive, group, mid, media_ids):
    async def prepare():
        activity.phase(group, mid, '准备当前请求的图片附件')
        await asyncio.gather(*(adapter.images.cache_message(archive, group, target)
                               for target in media_ids))
        for target in media_ids:
            if any(s.get('type') in ('record', 'audio', 'ptt') for s in archive.segments(target)):
                activity.phase(group, mid, '转写当前请求的语音附件')
                await adapter.transcribe(archive, target)
    try:
        await asyncio.wait_for(prepare(), PREPARATION_SECONDS)
        return {'status': 'finished', 'notice': 'Check each attachment status; finished does not imply every attachment succeeded.'}
    except TimeoutError:
        return {'status': 'deadline_reached',
                'notice': 'Media preparation stopped at its deadline. Use available attachments; retrieve missing images explicitly or explain the missing audio. Do not claim it is still loading.'}
