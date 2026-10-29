"""Test native summarization on a read-only session snapshot, never the live store."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
import json
import logging
import os
from pathlib import Path
import shutil
import time


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--snapshot',required=True);parser.add_argument('--session',required=True)
    args=parser.parse_args()
    cfg=json.loads(Path(os.environ['QQ_CONFIG']).read_text())
    assert cfg['bot_qq']==99 and cfg['trusted_groups']==[900001,900002]
    from hermes_constants import set_hermes_home_override,reset_hermes_home_override
    from hermes_cli.plugins import get_plugin_manager
    from gateway.session_context import set_session_vars,clear_session_vars
    from hermes_state import SessionDB
    from agent.context_compressor import ContextCompressor
    from agent.model_metadata import estimate_messages_tokens_rough
    from agent.auxiliary_client import _build_call_kwargs
    from qqhermes import activity
    profile=Path(os.environ['HERMES_HOME'])/'profiles/qq-900001'
    override=set_hermes_home_override(profile)
    tokens=set_session_vars(platform='qq_onebot',chat_id='900001',chat_type='group',user_id='123456',message_id='compression-smoke')
    manager=get_plugin_manager()
    try:
        manager.discover_and_load()
        for logger_name in ('agent.context_compressor','agent.auxiliary_client'):
            logger=logging.getLogger(logger_name);logger.setLevel(logging.INFO);logger.addHandler(logging.StreamHandler())
        config=json.loads((profile/'config.yaml').read_text())
        aux=config['auxiliary']['compression']
        wire=_build_call_kwargs('deepseek','deepseek-flash',[],temperature=None,max_tokens=None,
            tools=None,timeout=aux['timeout'],extra_body=aux['extra_body'],reasoning_config=None,
            base_url=aux['base_url'],task='compression',no_progress_timeout=None)
        assert wire['extra_body']['thinking']['type']=='disabled',wire['extra_body']
        assert wire['extra_body']['max_tokens']==16384,wire['extra_body']
        # SQLite may need WAL sidecars even for a read-only connection to a WAL DB.
        # Give the disposable copy a writable parent; the fixture stays mounted RO.
        scratch=Path('/tmp/compression-session-copy.db')
        shutil.copyfile(args.snapshot,scratch)
        db=SessionDB(scratch,read_only=True)
        # Use a bounded historical test fixture, including archived turns: the live
        # session may already be compacted and have no eligible summary window.
        # This expanded transcript is NEVER saved or used to resume the live agent.
        messages=db.get_messages_as_conversation(args.session,include_ancestors=True,
                                                include_compacted=True)[-400:]
        db.close()
        assert len(messages)>40,len(messages)
        before=estimate_messages_tokens_rough(messages)
        compressor=ContextCompressor(model='deepseek-flash',provider='deepseek',base_url=aux['base_url'],
            api_key=os.environ['DEEPSEEK_API_KEY'],config_context_length=1048576,
            threshold_tokens_cap=config['compression']['threshold_tokens'],quiet_mode=False)
        activity.begin(900001,'compression-smoke','123456')
        start=time.monotonic()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future=executor.submit(copy_context().run,compressor.compress,messages,current_tokens=before,force=True)
            observed=False
            while not future.done():
                if '正在压缩会话上下文' in activity.render(900001,0):observed=True
                time.sleep(.2)
            compressed=future.result()
        after=estimate_messages_tokens_rough(compressed)
        print(json.dumps({'before':before,'after':after,'elapsed':round(time.monotonic()-start,1),
                          'activity_steps':len(activity.snapshot(900001)['steps']),
                          'summary_error':getattr(compressor,'_last_summary_error',None)}),flush=True)
        assert observed,'Native compression observer did not identify the active conversation'
        assert after<before,(before,after,getattr(compressor,'_last_summary_error',None))
        assert not getattr(compressor,'_last_summary_error',None),compressor._last_summary_error
        print(json.dumps({'compression':'passed','elapsed_seconds':round(time.monotonic()-start,1),
            'before_tokens':before,'after_tokens':after,'messages_before':len(messages),'messages_after':len(compressed),
            'thinking':'disabled','live_store_modified':False,'status_observed_compression':observed}),flush=True)
    finally:
        activity.finish(900001,'compression-smoke')
        manager.unload();clear_session_vars(tokens);reset_hermes_home_override(override)


if __name__=='__main__':main()
