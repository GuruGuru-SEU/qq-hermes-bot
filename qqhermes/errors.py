"""Tool failures carry backend correlation IDs without echoing signed URLs."""
import logging
import secrets

LOG = logging.getLogger('qq-hermes')


def tool_failure(exc, operation):
    identity = secrets.token_hex(6)
    # Keep exception details (which may include signed URLs) out of the prompt.
    LOG.error('tool_failed diagnostic_id=%s operation=%s kind=%s',
              identity, operation, type(exc).__name__, exc_info=True)
    from httpx import TimeoutException, TransportError, HTTPStatusError
    status = exc.response.status_code if isinstance(exc, HTTPStatusError) else None
    retryable = isinstance(exc, (TimeoutError, TimeoutException, TransportError)) or status in (429, 502, 503, 504)
    return {'success': False, 'error': type(exc).__name__,
            'code': 'http_error' if status else 'tool_error', 'http_status': status,
            'retryable': retryable, 'diagnostic_id': identity}
