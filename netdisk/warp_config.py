# warp_config.py
# 路径常量、平台判断、进程表、配置读写、端口探测
# 【重要】WIREPROXY_PROCESSES 是可变 dict，所有跨模块访问请用：
#         import warp_config
#         warp_config.WIREPROXY_PROCESSES[...] = ...
#         不要 from warp_config import WIREPROXY_PROCESSES 后重绑定

import os
import json
import socket
import platform

HOME_DIR = os.path.expanduser("~")
CONFIG_FILE = os.path.join(HOME_DIR, "config.json")
WARP_DIR = os.path.join(HOME_DIR, "warp_engine")
os.makedirs(WARP_DIR, exist_ok=True)

WIREPROXY_PROCESSES = {}
IS_WINDOWS = platform.system().lower() == 'windows'


def get_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if "proxies" not in data: data["proxies"] = []
                return data
        except: pass
    return {"current": "", "accounts": {}, "proxies": []}


def save_config(cfg):
    tmp_file = CONFIG_FILE + ".tmp"
    with open(tmp_file, 'w', encoding='utf-8') as f: json.dump(cfg, f, ensure_ascii=False, indent=4)
    os.replace(tmp_file, CONFIG_FILE)


def is_port_open(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(('127.0.0.1', port)) == 0


def is_port_available(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(('127.0.0.1', port))
            return True
        except OSError:
            return False