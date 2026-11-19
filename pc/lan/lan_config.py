"""读取局域网地址配置 pc/lan/lan_config.json（换局域网时只改这个文件）。只用标准库，不连接任何设备。"""
import json
from pathlib import Path
from urllib.parse import urlsplit

PATH = Path(__file__).resolve().parent / 'lan_config.json'
KEYS = {'board', 'block_detect_url', 'gesture_url'}
DEFAULTS = dict(board='http://192.168.137.100:8080', block_detect_url='', gesture_url='')


def _url(value, key):
    if value == '':
        return ''
    parts = urlsplit(value) if isinstance(value, str) else None
    if not parts or parts.scheme != 'http' or not parts.hostname or parts.path not in ('', '/') or parts.query:
        raise ValueError(f'{key} 须为 http://IP:端口 形式（或留空 ""），现在是 {value!r}')
    return value.rstrip('/')


def load(path=PATH):
    """返回 {board, block_detect_url, gesture_url}；文件不存在时用默认值（识别服务为空 = 本机模型）。"""
    path = Path(path)
    if not path.exists():
        return dict(DEFAULTS)
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except ValueError as exc:
        raise ValueError(f'{path} 不是合法 JSON：{exc}') from None
    if not isinstance(data, dict):
        raise ValueError(f'{path} 须为 JSON 对象')
    unknown = {k for k in data if not k.startswith('_')} - KEYS
    if unknown:
        raise ValueError(f'{path} 含未知字段 {sorted(unknown)}；可用字段：{sorted(KEYS)}')
    config = dict(DEFAULTS)
    for key in KEYS & set(data):
        config[key] = _url(data[key], key)
    if not config['board']:
        raise ValueError('board（开发板地址）不能为空')
    return config
