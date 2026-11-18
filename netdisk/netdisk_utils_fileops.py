# netdisk_utils_fileops.py
# IA 文件删除、重命名（含冷却/配额）、标签更新

import os
import time
import logging

import requests
import internetarchive as ia

from netdisk_state import cfg_get
from netdisk_utils_common import (
    _is_system_file,
    RENAME_LOCK, RENAME_COOLDOWN_MAP, RENAME_ACCOUNT_HISTORY,
)
from netdisk_utils_net import (
    ensure_ia_config, IA_LIMITER,
    get_req_proxies, get_ssl_verify, _get_s3_credentials,
)


def _delete_ia_file(identifier, filename):
    if not filename or _is_system_file(filename): return False
    if not ensure_ia_config(): return False

    max_attempts = 5
    for attempt in range(max_attempts):
        IA_LIMITER.acquire()
        req_proxies = get_req_proxies()
        old_http = os.environ.get('http_proxy')
        old_https = os.environ.get('https_proxy')

        if req_proxies:
            os.environ['http_proxy'] = req_proxies['http']
            os.environ['https_proxy'] = req_proxies['https']
        else:
            os.environ.pop('http_proxy', None)
            os.environ.pop('https_proxy', None)

        try:
            ia.delete(identifier, files=[filename], verbose=False)
            return True
        except Exception as e:
            err_msg = str(e).lower()
            if 'not found' in err_msg or '404' in err_msg:
                logging.info(f"文件 {filename} 在云端已不存在，视为删除成功。")
                return True
            if any(x in err_msg for x in ['too many requests', '503', '429', 'reduce your request rate']):
                if attempt < max_attempts - 1:
                    wait_s = cfg_get('ia_429_backoff', 60)
                    logging.warning(f"删除文件 {filename} 触发风控，等待 {wait_s} 秒重试 ({attempt+1}/{max_attempts})...")
                    time.sleep(wait_s)
                    continue
            else:
                if attempt < max_attempts - 1:
                    time.sleep(5)
                    continue
            logging.error(f"删除文件失败 {filename}: {e}")
            return False
        finally:
            if old_http: os.environ['http_proxy'] = old_http
            else: os.environ.pop('http_proxy', None)
            if old_https: os.environ['https_proxy'] = old_https
            else: os.environ.pop('https_proxy', None)
    return False


def _rename_cooldown_ok(identifier):
    now = time.time()
    with RENAME_LOCK: t = RENAME_COOLDOWN_MAP.get(identifier, 0)
    return now - t >= cfg_get('rename_cooldown', 300)


def _rename_cooldown_set(identifier):
    with RENAME_LOCK: RENAME_COOLDOWN_MAP[identifier] = time.time()


def _rename_account_quota_ok(owner):
    if not owner: return True, ""
    now = time.time()
    with RENAME_LOCK:
        hist = [t for t in RENAME_ACCOUNT_HISTORY[owner] if now - t < 3600]
        RENAME_ACCOUNT_HISTORY[owner] = hist
        limit = cfg_get('rename_account_hourly_limit', 5)
        if len(hist) >= limit: return False, f"⏳ 本账号每小时最多 {limit} 次重命名，请稍后"
    return True, ""


def _rename_account_record(owner):
    if owner:
        with RENAME_LOCK: RENAME_ACCOUNT_HISTORY[owner].append(time.time())


def _rename_ia_file(identifier, old_name, new_name, owner):
    if not identifier or not old_name or not new_name: return False, "参数缺失"
    if not _rename_cooldown_ok(identifier): return False, f"⏳ 重命名冷却中，请 {cfg_get('rename_cooldown', 300)} 秒后重试"
    ok, msg = _rename_account_quota_ok(owner)
    if not ok: return False, msg
    allow_tasks = cfg_get('rename_allow_tasks_php', False)
    show_hint = cfg_get('rename_show_official_hint', True)
    if not allow_tasks:
        return False, f"IA 官方不支持在线改名。请前往 https://archive.org/details/{identifier} 网页手动操作，或在设置中启用「允许自动改名」。"
    try:
        if not ensure_ia_config(): return False, "配置文件生成失败"
        ak, sk = _get_s3_credentials()
        if not ak: return False, "无法获取 S3 Token"
        IA_LIMITER.acquire()
        url = "https://archive.org/services/tasks.php"
        payload = {"cmd": "rename.php", "identifier": identifier, "oldname": old_name, "newname": new_name, "access": ak, "secret": sk}
        headers = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json", "Accept": "application/json"}
        req_proxies = get_req_proxies()
        resp = requests.post(url, json=payload, headers=headers, timeout=cfg_get('ia_timeout', 30), verify=get_ssl_verify(), proxies=req_proxies)
        if resp.status_code in (200, 201, 202):
            try:
                res_json = resp.json()
                if not res_json.get("success", True):
                    err_detail = res_json.get('error', '未知错误')
                    if "Unauthorized" in err_detail: return False, "❌ 权限被拒：当前账号没有修改该条目的权限。"
                    return False, f"服务器拒绝: {err_detail}"
            except: pass
            _rename_cooldown_set(identifier)
            _rename_account_record(owner)
            base = "✅ 重命名已提交"
            if show_hint: base += f"（如失败请前往 https://archive.org/details/{identifier} 手动操作）"
            return True, base
        elif resp.status_code == 429:
            IA_LIMITER.trigger_429()
            return False, "❌ 请求过多，已触发冷却，请稍后再试"
        elif resp.status_code == 401: return False, "❌ 鉴权失败 (401)：您的 S3 密钥无效。"
        else: return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
    except Exception as e: return False, f"请求异常: {str(e)}"


def _push_tags_update(identifier, new_tags):
    try:
        if not ensure_ia_config(): return False, "配置文件生成失败"
        IA_LIMITER.acquire()
        ia.modify_metadata(identifier, metadata={'subject': new_tags}, verbose=False)
        return True, "Tags 更新成功"
    except Exception as e:
        return False, str(e)