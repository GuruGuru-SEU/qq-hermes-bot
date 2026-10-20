"""Fail clearly instead of turning malformed calls into unfiltered archive dumps."""
import copy
import json

FIELDS = {'action','date','start','end','user','contains','message_id','limit','offset',
          'order','sample','seed','exclude_user','members','name','export','format','context'}


def validate_history(args):
    if not isinstance(args,dict) or args.get('action') not in ('search','stats','recent'):
        raise ValueError('qq_history requires explicit action: search, stats or recent; no query was executed')
    unknown=set(args)-FIELDS
    if unknown:raise ValueError('Unknown history parameters: '+', '.join(sorted(unknown)))
    for key in ('limit','offset','user','sample','context'):
        if key in args and type(args[key]) is not int:raise ValueError(key+' must be an integer')
    for key in ('date','start','end','contains','message_id','order','seed','name','export','format'):
        if key in args and not isinstance(args[key],str):raise ValueError(key+' must be text')
    if 'members' in args and type(args['members']) is not bool:raise ValueError('members must be boolean')
    if 'exclude_user' in args and (not isinstance(args['exclude_user'],list) or
                                  any(type(v) is not int for v in args['exclude_user'])):
        raise ValueError('exclude_user must be a list of QQ numbers')
    if args['action'] in ('search','recent') and set(args)&{'members','name'}:
        raise ValueError('members and name require action stats; use user for sender filtering')
    if args['action']=='stats' and set(args)&{'order','sample','seed','context'}:
        raise ValueError('order, sample, seed and context require action search')
    if not 1<=args.get('limit',20)<=200 or args.get('offset',0)<0:
        raise ValueError('limit must be 1..200; offset must be nonnegative')
    selectors={'date','start','end','user','contains','message_id','sample'}
    if args['action']=='search' and not any(args.get(k) not in (None,'') for k in selectors):
        raise ValueError('Filtered search requires a date, user, contains, message_id or sample inside arguments. For intentional unfiltered messages use action recent.')


def wrapper_error(name,args):
    if name=='qq_history':
        try:validate_history(args)
        except ValueError as exc:return str(exc)+' Put parameters inside the arguments object.'
    if name!='tool_call':return
    if not isinstance(args,dict):return 'tool_call requires an object'
    calls=args.get('calls',[args] if 'name' in args else [])
    if isinstance(calls,str):
        try:calls=json.loads(calls)
        except ValueError:return 'calls must be JSON objects containing name and arguments'
    if isinstance(calls,dict):calls=[calls]
    if not isinstance(calls,list):return 'calls must be an array'
    for call in calls:
        if not isinstance(call,dict):continue
        if not str(call.get('name','')).startswith('qq_'):continue
        if set(call)-{'name','arguments'} or 'arguments' not in call:
            return ('Malformed QQ tool call: no tool was executed. Put ALL parameters inside arguments. '
                    'Correct shape: {"calls":[{"name":"qq_history","arguments":{"action":"search","contains":"term","limit":20}}]}')
        child=call['arguments']
        if isinstance(child,str):
            try:child=json.loads(child)
            except ValueError:return 'arguments must be a JSON object'
        if not isinstance(child,dict):return 'QQ tool arguments must be an object'
        error=wrapper_error(call['name'],child)
        if error:return error


def bounded_history(result,args,budget=12000):
    result=copy.deepcopy(result)
    result['applied_filters']={k:v for k,v in args.items() if k not in ('export','format')}
    rows=result.get('messages',[])
    trimmed=False
    for row in rows:
        cap=4000 if args.get('message_id') else 800
        if len(row.get('text',''))>cap:
            row['text']=row['text'][:cap]+'… [use message_id or export for full text]'
            row['text_truncated']=True;trimmed=True
    size=lambda:len(json.dumps(result,ensure_ascii=False,separators=(',',':')))
    # Neighbor context is optional and must not crowd out the selected hits.
    if size()>budget and result.get('context_messages'):
        result['context_messages']=[];result['context_truncated']=True;trimmed=True
    while size()>budget and len(rows)>1:
        rows.pop();trimmed=True
    members=result.get('members',[])
    while size()>budget and len(members)>1:
        members.pop();trimmed=True
    if size()>budget:
        for row in rows:
            for key in ('images','files','reply_to'):
                if row.get(key):
                    row[key+'_omitted']=len(row[key]);row[key]=[];trimmed=True
            if len(row.get('name',''))>200:row['name']=row['name'][:200];trimmed=True
        for member in members:
            member['names']=[n[:200] for n in member.get('names',[])[:5]]
            member['names_truncated']=True;trimmed=True
    available={r['message_id'] for r in rows+result.get('context_messages',[])}
    for row in rows:
        if 'context_ids' in row:
            original=row['context_ids']
            row['context_ids']=[mid for mid in original if mid in available]
            if len(original)!=len(row['context_ids']):row['context_truncated']=True;trimmed=True
    if trimmed:
        result['response_truncated']=True
        result['notice']=result.get('notice','')+' Bounded preview. Use message_id, pagination, smaller context, qq_file, or export for complete records.'
        if rows and not args.get('sample'):
            next_offset=args.get('offset',0)+len(rows)
            result['next_offset']=next_offset if next_offset<result.get('total',0) else None
        elif members:
            next_offset=args.get('offset',0)+len(members)
            result['next_offset']=next_offset if next_offset<result.get('member_count',0) else None
    if 'selection' in result:
        result['selection']['returned']=len(rows)
    return result
