"""Offline profile edits use the same columns and limits as the game protocol."""
from pathlib import Path
import hashlib
import os
import tempfile
import player_store as store

NAME_MIN, NAME_MAX, SIGNATURE_MAX = 2, 7, 30
MAX_IMAGE_BYTES = 5 * 1024 * 1024
GAME_ROOT = Path(__file__).resolve().parent.parent


def avatar_choices(root=None):
    heads = Path(root or GAME_ROOT) / 'webroot/image/head'
    return [(0, 'Default Avatar')] + [(int(p.stem), 'Avatar %s' % p.stem)
                                for p in sorted(heads.glob('*.png'), key=lambda p: int(p.stem) if p.stem.isascii() and p.stem.isdigit() else -1)
                                if p.stem.isascii() and p.stem.isdigit() and int(p.stem) > 0]


def _text(value, label, maximum, minimum=0):
    if not isinstance(value, str):
        raise ValueError('%s must be text.' % label)
    try:
        length = len(value.encode('utf-16-le')) // 2
    except UnicodeError:
        raise ValueError('%s contains invalid characters.' % label) from None
    if not minimum <= length <= maximum or any(ord(c) < 32 for c in value):
        raise ValueError('%s must be %d–%d characters long and cannot contain line breaks or control characters.' % (label, minimum, maximum))
    return value


def validate_name(con, player, value):
    """Validate a new name while the caller holds the player write transaction.

    Existing names (including legacy duplicates/long generated names) are kept
    when editing other fields. SQLite NOCASE only folds ASCII, so use Unicode
    casefold for new names in every offline profile editor.
    """
    if not isinstance(value, str):
        raise ValueError('Name must be text.')
    name = value.strip()
    if name != player['name']:
        _text(name, 'Name', NAME_MAX, NAME_MIN)
        folded = name.casefold()
        if any(str(row['name'] or '').casefold() == folded for row in con.execute(
                'SELECT name FROM players WHERE uin<>?', (int(player['uin']),))):
            raise ValueError('That name is already used by another account.')
    return name


def sync_name(con, player, name):
    """Update live social labels using the stored UIN/role ID, never old text."""
    con.execute('UPDATE friends SET friend_name=? WHERE friend_role_id=?',
                (name, player['role_id']))
    if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='local_guild_members'").fetchone():
        con.execute('UPDATE local_guild_members SET name=? WHERE uin=?',
                    (name, player['uin']))


def _image(path):
    source = Path(path).expanduser()
    if not source.is_file() or not 0 < source.stat().st_size <= MAX_IMAGE_BYTES:
        raise ValueError('Choose a PNG or JPG image no larger than 5 MiB.')
    data = source.read_bytes()
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError('Avatar images must not exceed 5 MiB.')
    if data.startswith(b'\x89PNG\r\n\x1a\n') and data.endswith(b'IEND\xaeB`\x82'):
        suffix = '.png'
    elif data.startswith(b'\xff\xd8\xff') and data.endswith(b'\xff\xd9'):
        suffix = '.jpg'
    else:
        raise ValueError('The avatar file is not a valid PNG or JPG image.')
    return data, suffix


def prepare_profile(uin, request, root=None):
    if not isinstance(request, dict):
        raise ValueError('Invalid profile format.')
    root = Path(root or GAME_ROOT).resolve()
    with store._db() as con:
        row = con.execute('SELECT * FROM players WHERE uin=?', (int(uin),)).fetchone()
        player = dict(row) if row else None
    if not player:
        raise ValueError('Account does not exist.')
    name = request.get('name', player['name'])
    if not isinstance(name, str):
        raise ValueError('Name must be text.')
    name = name.strip()
    # Legacy generated names can exceed seven characters. Preserve them when
    # editing only the avatar/signature; new names follow the original limit.
    if name != player['name']:
        _text(name, 'Name', NAME_MAX, NAME_MIN)
    signature = _text(request.get('signature', player.get('signature') or ''), 'Signature', SIGNATURE_MAX)
    mode = request.get('avatar', 'keep')
    fields = {'name': name, 'signature': signature}
    data = suffix = None
    if mode == 'custom':
        source = request.get('image_path')
        if not isinstance(source, str) or not source:
            raise ValueError('Select an avatar image first.')
        data, suffix = _image(source)
    elif mode != 'keep':
        if type(mode) is not int or mode not in dict(avatar_choices(root)):
            raise ValueError('Select an existing avatar.')
        fields.update(face_id=mode, qq_face_url='')
    return player, root, fields, data, suffix


def save_profile(uin, request, root=None, created_paths=None):
    player, root, fields, data, suffix = prepare_profile(uin, request, root)
    name, signature = fields['name'], fields['signature']
    created = None
    try:
        with store.atomic_player(player) as con:
            fields['name'] = name = validate_name(con, player, name)
            if data is not None:
                folder = root/'webroot/image/head/custom'
                folder.mkdir(parents=True, exist_ok=True)
                target = folder/('%d_%s%s' % (int(player['uin']), hashlib.sha256(data).hexdigest(), suffix))
                assert target.resolve().is_relative_to(folder.resolve())
                if not target.exists():
                    temporary = None
                    try:
                        with tempfile.NamedTemporaryFile(dir=folder, suffix='.tmp', delete=False) as stream:
                            temporary = Path(stream.name);stream.write(data)
                        os.replace(temporary, target);created = target
                    finally:
                        if temporary and temporary.exists():
                            temporary.unlink()
                fields.update(face_id=0, qq_face_url='http://127.0.0.1:8080/image/head/custom/'+target.name)
            store.save_player(player['uin'], **fields)
            sync_name(con, player, name)
    except BaseException:
        if created and created.exists():
            created.unlink()
        raise
    if created is not None and created_paths is not None:
        created_paths.append(created)
    return {'name': name, 'signature': signature,
            'face_id': fields.get('face_id', int(player.get('face_id') or 0)),
            'qq_face_url': fields.get('qq_face_url', player.get('qq_face_url') or '')}
