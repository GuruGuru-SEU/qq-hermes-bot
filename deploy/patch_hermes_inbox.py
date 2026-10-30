"""Pinned model-boundary input delivery, never an auxiliary answer reviewer."""
from pathlib import Path

path = Path('/opt/hermes/agent/conversation_loop.py')
source = path.read_text()
changes = [(
    '        _run_phase(prepare_iteration, agent, s)\n',
    '        import sys\n'
    '        qq_inbox = sys.modules.get("_qq_hermes_live_inbox")\n'
    '        if qq_inbox is not None:\n'
    '            qq_inbox.boundary(agent, s.messages)\n'
    '        _run_phase(prepare_iteration, agent, s)\n'), (
    '            _v = _run_phase(\n                run_tool_round if s.assistant_message.tool_calls else finish_text_response, agent, s\n            )',
    '            # QQ: reconsider unexecuted output if a follow-up arrived during generation.\n'
    '            if qq_inbox is not None and qq_inbox.boundary(agent, s.messages, final=not bool(s.assistant_message.tool_calls)):\n'
    '                s.final_response = None\n'
    '                continue\n'
    '            _v = _run_phase(\n                run_tool_round if s.assistant_message.tool_calls else finish_text_response, agent, s\n            )')]
for anchor, replacement in changes:
    if replacement in source:
        continue
    if source.count(anchor) != 1:
        raise RuntimeError('Hermes inbox boundary changed; review the integration before upgrading')
    source = source.replace(anchor, replacement)
path.write_text(source)
print('QQ live inbox model-boundary patch verified')
