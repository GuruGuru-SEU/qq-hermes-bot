"""Build-time Chromium identity shared by all managed browser sessions."""
from pathlib import Path
import re
import subprocess

IDENTITY_FILE = Path(__file__).with_name('browser-user-agent.txt')


def chrome_user_agent(version_output):
    match = re.match(r'Chromium (\d+)\.\d+\.\d+\.\d+(?:\s|$)', version_output.strip())
    if not match:
        raise ValueError('Could not determine installed Chromium version.')
    # Match Chrome UA reduction; retain Linux and the actual browser major.
    return ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
            f'(KHTML, like Gecko) Chrome/{match[1]}.0.0.0 Safari/537.36')


def user_agent():
    try:
        value = IDENTITY_FILE.read_text().strip()
    except OSError as exc:
        raise ValueError('Browser identity missing; rebuild the bot image.') from exc
    if not re.fullmatch(r'Mozilla/5\.0 \(X11; Linux x86_64\) AppleWebKit/537\.36 '
                        r'\(KHTML, like Gecko\) Chrome/\d+\.0\.0\.0 Safari/537\.36', value):
        raise ValueError('Invalid managed browser identity; rebuild the bot image.')
    return value


if __name__ == '__main__':
    version = subprocess.check_output(['/usr/bin/chromium', '--version'], text=True, timeout=10)
    IDENTITY_FILE.write_text(chrome_user_agent(version) + '\n')
