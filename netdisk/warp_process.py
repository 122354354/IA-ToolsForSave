# warp_process.py
# 停止进程 / 启动时自动拉起已保存节点
# 【重要】所有对进程表的操作都通过 warp_config.WIREPROXY_PROCESSES 模块属性访问

import os
import subprocess
import logging

try:
    import certifi
except ImportError:
    certifi = None

import warp_config
from warp_config import WARP_DIR, IS_WINDOWS, get_config, is_port_open


def stop_warp(port):
    if port in warp_config.WIREPROXY_PROCESSES:
        try:
            warp_config.WIREPROXY_PROCESSES[port].terminate()
            warp_config.WIREPROXY_PROCESSES[port].wait(timeout=2)
        except:
            try: warp_config.WIREPROXY_PROCESSES[port].kill()
            except: pass
        del warp_config.WIREPROXY_PROCESSES[port]


def autostart_saved_proxies():
    cfg = get_config()
    for p in cfg.get('proxies', []):
        url = p.get('url', '')
        if '127.0.0.1' in url:
            try:
                port = int(url.split(':')[-1])
                if url.startswith('http://'): port -= 1

                if port in warp_config.WIREPROXY_PROCESSES: continue
                if is_port_open(port): continue

                name = p.get('name', '')
                bin_path = None
                cmd = []

                if 'M' in name:
                    bin_path = os.path.join(WARP_DIR, "usque.exe" if IS_WINDOWS else "usque")
                    if os.path.exists(bin_path):
                        env = os.environ.copy()
                        env["GODEBUG"] = "netdns=cgo"
                        try:
                            env["SSL_CERT_FILE"] = certifi.where()
                            env["SSL_CERT_DIR"] = os.path.dirname(certifi.where())
                        except: pass
                        cmd = [bin_path, "socks", "-c", os.path.join(WARP_DIR, "config.json"), "-b", "127.0.0.1", "-p", str(port), "-s", "cloudflare.com", "--connect-port", "443"]
                        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=WARP_DIR, env=env)
                        warp_config.WIREPROXY_PROCESSES[port] = proc

                elif 'W' in name:
                    bin_path = os.path.join(WARP_DIR, "sing-box.exe" if IS_WINDOWS else "sing-box")
                    conf_path = os.path.join(WARP_DIR, f"warp_{port}.json")
                    if os.path.exists(bin_path) and os.path.exists(conf_path):
                        cmd = [bin_path, "run", "-c", conf_path]
                        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=WARP_DIR)
                        warp_config.WIREPROXY_PROCESSES[port] = proc
            except: pass