"""Unambiguous outbound results for Hermes's tool-failure classifier."""


def send_result(result,kind):
    if result.success:
        value={'success':True,'kind':kind,'delivery':'uploaded' if kind=='file' else 'sent'}
        if result.message_id:value['message_id']=result.message_id
        return value
    value={'success':False,'kind':kind,'error':result.error or 'Send did not succeed',
           'retryable':bool(result.retryable)}
    if result.error_kind:value['error_kind']=result.error_kind
    if result.retry_after is not None:value['retry_after']=result.retry_after
    return value


def http_send_result(response):
    try:value=response.json()
    except ValueError:value={}
    if not isinstance(value,dict):value={}
    if response.is_success and value.get('success') is True:
        # Also tolerate older internal endpoints without exposing null error fields.
        return {k:v for k,v in value.items() if k not in ('error','error_kind','retry_after') and v is not None}
    return {**value,'success':False,'error':value.get('error') or f'Send endpoint returned HTTP {response.status_code}',
            'http_status':response.status_code}
