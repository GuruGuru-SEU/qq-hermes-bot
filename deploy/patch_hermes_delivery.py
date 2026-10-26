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
