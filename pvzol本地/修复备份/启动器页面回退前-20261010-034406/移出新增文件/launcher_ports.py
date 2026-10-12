"""Validated, portable launcher service-port configuration."""
from __future__ import annotations
import os
import socket

DEFAULT_PORTS = {'web': 8080, 'admin': 8081, 'game': 9001, 'policy': 843}
PORT_TITLES = {'web': '游戏资源', 'admin': 'GM 后台', 'game': '游戏服务', 'policy': 'Flash 连接策略'}


def validate_ports(value, base=None):
    if not isinstance(value, dict) or any(key not in DEFAULT_PORTS for key in value):
        raise ValueError('端口设置格式无效。')
    result = dict(DEFAULT_PORTS if base is None else base)
    result.update(value)
    for key, port in result.items():
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError('%s端口必须为 1 至 65535 的整数。' % PORT_TITLES[key])
    if result['policy'] != 843:
        raise ValueError('Flash 连接策略端口固定为 843，不能修改。')
    if len(set(result.values())) != len(result):
        raise ValueError('游戏资源、GM 后台、游戏服务与策略端口不能重复。')
    return result


def port_environment(ports):
    return {'PVZ_%s_PORT' % key.upper(): str(value)
            for key, value in validate_ports(ports).items()}


def ensure_port_available(key, port):
    """Check a candidate bind without listening, reusing or stopping a process."""
    address = '0.0.0.0' if key in ('game', 'policy') else '127.0.0.1'
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
            if os.name == 'nt' and hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                candidate.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            candidate.bind((address, port))
    except OSError as exc:
        raise ValueError('%s端口 %s 已被占用或无法绑定，请选择空闲端口。' % (PORT_TITLES[key], port)) from exc
