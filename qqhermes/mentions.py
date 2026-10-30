"""Explicit QQ mentions; plain text and display names never become pings."""
import re
import asyncio

from .conversation import conversation

MAX_MENTIONS = 5
MENTION_SCHEMA = {
    'type': 'array', 'maxItems': MAX_MENTIONS,
    'items': {'type': 'string', 'pattern': '^[1-9][0-9]{0,14}$'},
    'description': 'Optional QQ user IDs to @ in THIS group (maximum 5). Use observed sender/mention IDs; never guess names or use all. Omit in DMs. With qq_send, only text/image supports mentions.',
}


def normalize_mentions(chat, value=None):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_MENTIONS:
        raise ValueError('mention_user_ids must be an array of at most 5 QQ user IDs')
    result = []
    for user in value:
        if isinstance(user, bool) or not isinstance(user, (str, int)) or not re.fullmatch(r'[1-9][0-9]{0,14}', str(user)):
            raise ValueError('Mention targets must be numeric QQ user IDs; @all and display names are not supported')
        if str(user) not in result:
            result.append(str(user))
    if result and conversation(chat).kind != 'group':
        raise ValueError('Mentions are only supported in the current group, not DMs')
    return result


async def validate_members(adapter, chat, users):
    async def verify(user):
        try:
            info = await adapter.api('get_group_member_info',
                {**conversation(chat).recipient, 'user_id': int(user), 'no_cache': False}, timeout=10)
        except Exception as exc:
            raise ValueError(f'Could not verify QQ user {user} in this group; nothing was sent') from exc
        if not isinstance(info, dict) or str(info.get('user_id')) != user or str(info.get('group_id')) != str(chat):
            raise ValueError(f'QQ user {user} was not verified as a member of this group; nothing was sent')
    results = await asyncio.gather(*(verify(user) for user in users), return_exceptions=True)
    for result in results:
        if isinstance(result, BaseException):
            raise result


def mention_segments(users):
    return [segment for user in users for segment in (
        {'type': 'at', 'data': {'qq': user}}, {'type': 'text', 'data': {'text': ' '}})]


def incoming_mentions(event):
    return list(dict.fromkeys(str(s.get('data', {}).get('qq'))
        for s in event.get('message', []) if s.get('type') == 'at'
        and re.fullmatch(r'[1-9][0-9]{0,14}', str(s.get('data', {}).get('qq', '')))))
