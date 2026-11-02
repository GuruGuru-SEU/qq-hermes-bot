"""Pinned inbox and receipt completion boundaries; no answer reviewer."""
from pathlib import Path

path = Path('/opt/hermes/agent/conversation_loop.py')
source = path.read_text()
changes = [(
    '    _prelude_action, _prelude_result = play_prelude(agent, s, prelude)',
    '''    qq_completion = None
    _prelude_action, _prelude_result = play_prelude(agent, s, prelude)'''), (
    '        _run_phase(prepare_iteration, agent, s)',
    '''        import sys
        qq_inbox = sys.modules.get("_qq_hermes_live_inbox")
        if qq_inbox is not None:
            qq_inbox.boundary(agent, s.messages)
            qq_completion = qq_inbox.finish_ready(agent, s.messages)
            if qq_completion is not None:
                s.final_response = qq_completion["text"]
                s._turn_exit_reason = "text_response(qq_receipts)"
                break
        _run_phase(prepare_iteration, agent, s)'''), (
    '''            _v = _run_phase(
                run_tool_round if s.assistant_message.tool_calls else finish_text_response, agent, s
            )''',
    '''            if qq_inbox is not None and qq_inbox.boundary(agent, s.messages, final=not bool(s.assistant_message.tool_calls), response=s.assistant_message.content):
                s.final_response = None
                continue
            _v = _run_phase(
                run_tool_round if s.assistant_message.tool_calls else finish_text_response, agent, s
            )'''), (
    '            if _v.action == "return":',
    '''            if (_v.action == "continue" and s.assistant_message.tool_calls
                    and not s.failed and not s.interrupted and qq_inbox is not None):
                qq_inbox.boundary(agent, s.messages)
                qq_completion = qq_inbox.finish_ready(agent, s.messages)
                if qq_completion is not None:
                    s.final_response = qq_completion["text"]
                    s._turn_exit_reason = "text_response(qq_receipts)"
                    break
            if _v.action == "return":'''), (
    '    if s._compression_timeout_exhausted:',
    '''    if qq_completion is not None and result.get("completed") and not result.get("failed"):
        result["qq_completion"] = {k:v for k,v in qq_completion.items() if k != "text"}
    if s._compression_timeout_exhausted:''')]
for anchor, replacement in changes:
    if replacement in source:
        continue
    if source.count(anchor) != 1:
        raise RuntimeError('Hermes turn-loop contract changed; review before upgrading')
    source = source.replace(anchor, replacement)
path.write_text(source)
print('QQ inbox and explicit completion boundaries verified')
