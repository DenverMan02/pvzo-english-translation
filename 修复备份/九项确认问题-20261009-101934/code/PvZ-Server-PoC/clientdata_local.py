"""Persistent per-player key/value storage for Cmd_Get/SetClientData.

The client protocol uses signed protobuf ``int64`` for both keys and values.
The handlers store these pairs in an additive SQLite table keyed by the
authenticated player's ``uin``. Importing this module and calling ``register``
does not open or modify the database; the table is created on the first request.
"""

import contextlib
import sqlite3


_INT64_MIN = -(1 << 63)
_INT64_MAX = (1 << 63) - 1
_UINT64_MASK = (1 << 64) - 1


def _read_varint(data, pos):
    value = 0
    for shift in range(0, 70, 7):
        if pos >= len(data):
            raise ValueError("truncated protobuf varint")
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            if value > _UINT64_MASK:
                raise ValueError("protobuf varint exceeds 64 bits")
            return value, pos
    raise ValueError("protobuf varint is too long")


def _parse_int64_fields(data):
    """Return wire-0 fields as signed int64 values; reject malformed messages."""
    fields = {}
    pos = 0
    while pos < len(data):
        tag, pos = _read_varint(data, pos)
        field_num, wire = tag >> 3, tag & 7
        if field_num == 0:
            raise ValueError("invalid protobuf field number")
        if wire == 0:
            raw, pos = _read_varint(data, pos)
            fields[field_num] = raw - (1 << 64) if raw & (1 << 63) else raw
        elif wire == 1:
            pos += 8
        elif wire == 2:
            length, pos = _read_varint(data, pos)
            pos += length
        elif wire == 5:
            pos += 4
        else:
            raise ValueError("unsupported protobuf wire type")
        if pos > len(data):
            raise ValueError("truncated protobuf field")
    return fields


def _encode_varint(value):
    value &= _UINT64_MASK
    out = bytearray()
    while value >= 0x80:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def _int64_field(field_num, value):
    """Encode a protobuf int64 varint, including negative two's-complement values."""
    return _encode_varint((field_num << 3) | 0) + _encode_varint(value)


@contextlib.contextmanager
def _database(db_path):
    con = sqlite3.connect(db_path, timeout=10)
    try:
        con.execute(
            "CREATE TABLE IF NOT EXISTS local_client_data ("
            "uin INTEGER NOT NULL, \"key\" INTEGER NOT NULL, \"value\" INTEGER NOT NULL, "
            "PRIMARY KEY (uin, \"key\"))"
        )
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def _signed_int64(value):
    value = int(value)
    if value < _INT64_MIN or value > _INT64_MAX:
        raise ValueError("value is outside the signed int64 range")
    return value


def _player_uin(player):
    try:
        uin = int(player.get("uin"))
    except (AttributeError, TypeError, ValueError):
        return None
    # A missing identity must never share a synthetic uin=0 bucket.
    return uin if uin > 0 else None


def register(db_path):
    """Return dispatcher routes; ``db_path`` is normally ``player_store.DB_PATH``.

    The live dispatcher calls each route as ``handler(cmd_data, player)`` where
    ``cmd_data`` is the raw per-command protobuf body and ``player`` is the
    authenticated player dictionary containing its ``uin``.
    """

    def get_client_data(cmd_data, player):
        try:
            fields = _parse_int64_fields(cmd_data)
        except (TypeError, ValueError):
            fields = {}
        key = fields.get(1, 0)
        uin = _player_uin(player)
        value = 0
        if uin is not None:
            with _database(db_path) as con:
                row = con.execute(
                    "SELECT \"value\" FROM local_client_data WHERE uin=? AND \"key\"=?",
                    (uin, key),
                ).fetchone()
                if row is not None:
                    value = _signed_int64(row[0])
        return _int64_field(1, key) + _int64_field(2, value)

    def set_client_data(cmd_data, player):
        try:
            fields = _parse_int64_fields(cmd_data)
            key, value = fields[1], fields[2]
        except (KeyError, TypeError, ValueError):
            return b""
        uin = _player_uin(player)
        if uin is not None:
            with _database(db_path) as con:
                con.execute(
                    "INSERT INTO local_client_data (uin, \"key\", \"value\") VALUES (?, ?, ?) "
                    "ON CONFLICT(uin, \"key\") DO UPDATE SET \"value\"=excluded.\"value\"",
                    (uin, key, value),
                )
        return b""

    return {
        "PVZ.Cmd.Cmd_GetClientData_CS": get_client_data,
        "PVZ.Cmd.Cmd_SetClientData_CS": set_client_data,
    }
