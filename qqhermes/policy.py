"""Group-scoped consent for native delegation and outbound tool destinations."""
import json
from .settings import root
from .conversation import conversation_for_profile, conversation
from .store import Store


def identity():
    from gateway.session_context import get_session_env
    return conversation_for_profile().key,get_session_env('HERMES_SESSION_USER_ID',''),get_session_env('HERMES_SESSION_MESSAGE_ID','')


def before_tool(tool_name,args,**kwargs):
    if tool_name=='terminal':
        from .browser_policy import command_error
        error=command_error(args.get('command'))
        if error:return {'action':'block','message':error}
    # Hermes 0.22.5 exposes the compressed manager under this model-facing name.
    if tool_name=='cronjob_manage': tool_name='cronjob'
    from .validation import wrapper_error
    error=wrapper_error(tool_name,args)
    if error:return {'action':'block','message':error}
    if tool_name=='skill_manage':
        from .preferences import skill_conflict
        error=skill_conflict(args)
        if error:return {'action':'block','message':error}
    if tool_name not in ('delegate_task','send_message','cronjob'): return
    try: group,user,mid=identity()
    except PermissionError: return
    store=Store(root()/'transport.sqlite3')
    if tool_name=='send_message':
        target=str(args.get('target',''))
        if target not in (f'qq_onebot:{group}',):
            return {'action':'block','message':f'Only this conversation is permitted. Use target qq_onebot:{group}.'}
    if tool_name=='cronjob' and args.get('action') in ('create','update'):
        deliver=args.get('deliver','origin')
        failure_deliver=args.get('failure_deliver')
        if deliver not in (None,'origin',f'qq_onebot:{group}') or failure_deliver not in (None,'','local','origin',f'qq_onebot:{group}'):
            return {'action':'block','message':'Cron delivery must target the originating conversation.'}
    if tool_name!='delegate_task': return
    action=args.get('action','spawn')
    if action in ('list','stop','steer'):
        if action!='list':
            with store.db() as db:
                row=db.execute('SELECT * FROM children WHERE id=?',(args.get('subagent_id',''),)).fetchone()
            if not row or row['group_id']!=group or row['user_id']!=user:
                return {'action':'block','message':'Only the original requester may control this task.'}
        return
    if not user or not mid:
        return {'action':'block','message':'Background work requires an explicit request and subsequent confirmation from the requester.'}
    if store.consume(group,user,mid,args): return
    proposal=store.proposal(group,user,mid,args)
    address='@ the bot with ' if conversation(group).kind=='group' else 'reply with '
    return {'action':'block','message':f'Background task is proposed, NOT started. Explain the task and ask the requester to {address}/approve {proposal}. Only that requester can approve. Do not retry launching in this turn.'}


def after_tool(tool_name,args=None,result=None,**kwargs):
    if tool_name!='delegate_task': return
    try:
        group,user,mid=identity()
        value=json.loads(result) if isinstance(result,str) else result
        if not isinstance(value,dict): return
        ids=value.get('subagent_ids',[])
        store=Store(root()/'transport.sqlite3')
        with store.db() as db:
            for ident in ids: db.execute('INSERT OR REPLACE INTO children VALUES (?,?,?)',(str(ident),group,user))
    except (PermissionError,ValueError,TypeError): return
