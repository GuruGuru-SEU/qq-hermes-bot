"""Small syntax checks for browser CLI calls; no model calls or shell execution."""
import shlex


def command_error(command):
    if not isinstance(command, str) or 'agent-browser' not in command:
        return None
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return None  # The terminal reports malformed shell syntax itself.
    browser = False
    for i, token in enumerate(tokens):
        if token in (';', '&&', '||', '&', '(', ')'):
            browser = False
        if token.rsplit('/', 1)[-1] == 'agent-browser':
            browser = True
        if browser and (token in ('|', '|&') or (token == '/dev/null' and i and '>' in tokens[i-1])):
            return ('Do not pipe or discard agent-browser results: it hides failures and truncates navigation IDs. '
                    'Run each dependent browser command directly with &&; use diagnose for bounded errors/console/network, '
                    'snapshot options for page excerpts, or save full output to a local file and check its exit code. '
                    'Use checkpoint NAME / resume NAME to retain exact URLs instead of rebuilding them from previews.')
    return None
