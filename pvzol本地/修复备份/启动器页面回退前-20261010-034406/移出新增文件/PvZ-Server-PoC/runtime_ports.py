"""Server-side ports supplied by the portable launcher environment."""
import os
from urllib.parse import urlsplit, urlunsplit


def env_port(key, default):
    text = os.environ.get('PVZ_%s_PORT' % key.upper(), str(default))
    try:
        value = int(text)
    except (TypeError, ValueError):
        raise ValueError('PVZ_%s_PORT must be an integer' % key.upper()) from None
    if not 1 <= value <= 65535:
        raise ValueError('PVZ_%s_PORT must be between 1 and 65535' % key.upper())
    if key == 'policy' and value != 843:
        raise ValueError('Flash master socket-policy port must remain 843')
    return value


WEB_PORT = env_port('web', 8080)
ADMIN_PORT = env_port('admin', 8081)
GAME_PORT = env_port('game', 9001)
POLICY_PORT = env_port('policy', 843)


def web_url(path):
    return 'http://127.0.0.1:%d/%s' % (WEB_PORT, str(path).lstrip('/'))


def local_avatar_url(url):
    """Rebase previously saved local avatars without changing their saved values."""
    try:
        parsed = urlsplit(str(url))
    except (ValueError, TypeError):
        return str(url)
    if (parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost')
            and parsed.username is None and parsed.password is None
            and parsed.path.startswith('/image/head/')):
        return urlunsplit(('http', '127.0.0.1:%d' % WEB_PORT, parsed.path, parsed.query, ''))
    return str(url)
