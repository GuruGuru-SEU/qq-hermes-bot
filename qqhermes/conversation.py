"""Stable conversation identities; existing numeric group keys stay unchanged."""
from dataclasses import dataclass
from .settings import root, settings


@dataclass(frozen=True)
class Conversation:
    kind: str
    id: int

    @property
    def key(self):
        return f'dm-{self.id}' if self.kind == 'dm' else self.id

    @property
    def chat_id(self):
        return str(self.key)

    @property
    def profile(self):
        return f'qq-{self.key}'

    @property
    def directory(self):
        return root()/('users' if self.kind == 'dm' else 'groups')/str(self.id)

    @property
    def recipient(self):
        return {'user_id' if self.kind == 'dm' else 'group_id': self.id}

    @property
    def label(self):
        return f'QQ {"private chat" if self.kind == "dm" else "group"} {self.id}'

    def permitted(self, cfg):
        return self.id in map(int, cfg.get('trusted_users' if self.kind == 'dm' else 'trusted_groups', []))


def conversation(value):
    raw = str(value)
    kind, number = ('dm', raw[3:]) if raw.startswith('dm-') else ('group', raw)
    if not number.isascii() or not number.isdigit() or int(number) <= 0 or str(int(number)) != number:
        raise ValueError('Invalid QQ conversation ID')
    return Conversation(kind, int(number))


def conversations(cfg):
    return ([Conversation('group', int(g)) for g in cfg.get('trusted_groups', [])]
            + [Conversation('dm', int(u)) for u in cfg.get('trusted_users', [])])


def conversation_for_profile():
    from pathlib import Path
    from hermes_constants import get_hermes_home
    name = Path(get_hermes_home()).name
    for chat in conversations(settings()):
        if chat.profile == name:
            return chat
    raise PermissionError('QQ tools require a trusted conversation profile')


def event_conversation(data):
    if data.get('message_type') == 'private':
        # Temporary group-origin conversations are not personal friend sessions.
        if data.get('sub_type', 'friend') != 'friend':
            raise ValueError('Only direct friend messages are supported')
        return conversation(f'dm-{data.get("user_id", 0)}')
    return conversation(data.get('group_id', 0))


def parse_target(value):
    try:
        return conversation(value).chat_id, None
    except (ValueError, TypeError):
        return None


def validate_target(value):
    try:
        return conversation(value).permitted(settings()) or 'Untrusted QQ conversation'
    except (ValueError, TypeError):
        return 'Invalid QQ conversation ID'
