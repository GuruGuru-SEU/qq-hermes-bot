"""Pass stable ledger identity to QQ recovery; never re-send acknowledged bubbles."""
from pathlib import Path

path = Path('/opt/hermes/gateway/run_startup.py')
source = path.read_text()
anchor = '''            metadata = {"thread_id": row["thread_id"]} if row.get("thread_id") else None
            try:
                result = await adapter.send(chat_id=row["chat_id"], content=content, metadata=metadata)'''
replacement = '''            metadata = {"thread_id": row["thread_id"]} if row.get("thread_id") else None
            # qq-hermes-bot: retain per-bubble receipt identity during native ledger recovery.
            if row["platform"] == "qq_onebot":
                metadata = dict(metadata or {})
                metadata["qq_final_delivery"] = {"id": row["obligation_id"], "content": row["content"]}
            try:
                result = await adapter.send(chat_id=row["chat_id"], content=content, metadata=metadata)'''
if replacement in source:
    print('QQ delivery recovery patch already applied')
elif source.count(anchor) == 1:
    path.write_text(source.replace(anchor, replacement))
    print('QQ delivery recovery patch applied')
else:
    raise RuntimeError('Hermes delivery recovery changed; inspect before upgrading')

# Typed completion is backed by the report ledger; retain all native cleanup.
path = Path('/opt/hermes/gateway/run_turn_persistence.py')
source = path.read_text()
changes = [(
    '        _intentional_silence = self._is_intentional_silence(agent_result, response)',
    """        import sys
        _qq_inbox = sys.modules.get("_qq_hermes_live_inbox")
        _qq_completed = bool(_qq_inbox is not None and _qq_inbox.delivered_turn(source, agent_result))
        if _qq_completed:
            agent_result["already_sent"] = True
            agent_result["media_already_delivered"] = True
            response = ""
        _intentional_silence = self._is_intentional_silence(agent_result, response)"""), (
    '        if not _intentional_silence:',
    '        if not _intentional_silence and not _qq_completed:')]
for anchor, replacement in changes:
    if replacement in source:
        continue
    if source.count(anchor) != 1:
        raise RuntimeError('Hermes result shaping changed; inspect before upgrading')
    source = source.replace(anchor, replacement)
path.write_text(source)
print('QQ typed completion integration verified')
