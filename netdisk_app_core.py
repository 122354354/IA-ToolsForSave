# netdisk_app_core.py
# Flask app 创建、secret_key 加载/生成、蓝图注册、/favicon.ico 路由
# 【新增】/favicon.ico 路由返回空 204，避免浏览器控制台 "No favicon found" 报错

import logging
import secrets

from flask import Flask

import warp_module
from netdisk_core import get_config, save_config


def _load_or_create_secret_key():
    try:
        cfg = get_config()
        key = cfg.get('secret_key', '').strip()
        if key: return key
        key = secrets.token_hex(32)
        cfg['secret_key'] = key
        save_config(cfg)
        return key
    except Exception:
        return secrets.token_hex(32)


app = Flask(__name__)
app.secret_key = _load_or_create_secret_key()
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024 * 1024
app.register_blueprint(warp_module.warp_bp)


@app.route('/favicon.ico')
def favicon():
    """【新增】返回空 204，避免浏览器自动请求 favicon 时控制台报错"""
    return '', 204