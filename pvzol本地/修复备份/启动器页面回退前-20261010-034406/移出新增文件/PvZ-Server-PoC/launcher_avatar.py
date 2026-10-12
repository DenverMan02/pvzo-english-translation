"""Resolve the launcher's approved local avatar assets without arbitrary reads."""
from pathlib import Path
import re
from urllib.parse import urlsplit

MAX_AVATAR_BYTES = 5 * 1024 * 1024
_ASSET = re.compile(r'(?:[0-9]+\.png|unknow(?:1)?\.png|custom/[0-9]+_[a-f0-9]{64}\.(?:png|jpg|jpeg))\Z')


def asset_path(relative, game_root):
    """Return a verified PNG/JPEG under image/head, or None for any other file."""
    if not isinstance(relative, str) or not _ASSET.fullmatch(relative):
        return None
    heads = (Path(game_root) / 'webroot' / 'image' / 'head').resolve()
    candidate = (heads / relative).resolve()
    if not candidate.is_relative_to(heads):
        return None
    try:
        size = candidate.stat().st_size
        if not candidate.is_file() or not 0 < size <= MAX_AVATAR_BYTES:
            return None
        with candidate.open('rb') as stream:
            head = stream.read(8)
            stream.seek(-min(size, 12), 2)
            tail = stream.read(12)
        if relative.endswith('.png'):
            valid = head == b'\x89PNG\r\n\x1a\n' and tail.endswith(b'IEND\xaeB`\x82')
        else:
            valid = head.startswith(b'\xff\xd8\xff') and tail.endswith(b'\xff\xd9')
        return candidate if valid else None
    except OSError:
        return None


def avatar_relative(account, game_root):
    """Use the stored custom avatar, then face_id, then the game's default face."""
    try:
        custom = urlsplit(str(account.get('qq_face_url') or '').strip())
        if (custom.scheme == 'http' and custom.hostname in ('127.0.0.1', 'localhost')
                and custom.username is None and custom.password is None
                and custom.path.startswith('/image/head/')):
            relative = custom.path.removeprefix('/image/head/')
            if asset_path(relative, game_root):
                return relative
    except (TypeError, ValueError):
        pass
    try:
        face = int(account.get('face_id') or 0)
    except (ValueError, TypeError):
        face = 0
    relative = '%s.png' % face if face > 0 else 'unknow.png'
    return relative if asset_path(relative, game_root) else 'unknow.png'


def avatar_asset(account, game_root):
    return asset_path(avatar_relative(account, game_root), game_root)
