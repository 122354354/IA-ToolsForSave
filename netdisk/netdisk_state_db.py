# netdisk_state_db.py
# 配置读写、SQLite 连接、数据库初始化、账号与文件夹辅助函数
# 【B3-新 修复 - 2026-11】init_db 断电恢复时把 sub_tasks 的 uploading 改为 error
# 【G-15 改进 - 2026-11】init_db 断电恢复 SQL 分步骤 try/except 保护
# 【🐛 修复 - 2026-11】_safe_execute 的 if params: 改为 if params is not None:
# 【🔴 G-50 修复 - 2026-11】init_db 不清理 upload_tasks 表里 status='error' AND task_type='upload' 的记录
#                          （用户反复强制关网盘，upload_tasks 表里 status='error' 的 upload 任务无限累积
#                           → 数据库持续膨胀）
#                          修复：步骤 7 改为直接删除所有 task_type='upload' 的记录
#                          （upload 任务的恢复依赖 items 表 + 前端重试队列，
#                           不依赖 upload_tasks 表）
# 【🔴 G-70 修复 - 2026-11】get_folder_tree_options 的 conn.close() 加 try/finally
#                          （conn.execute 抛异常时 conn 泄漏）
#
# 【⏱️ 延迟与超时精细控制 - 2026-11 第 3/20 步】
# get_db() 的 timeout 从硬编码 30.0 改为读配置 db_timeout（默认 30，范围 5-300 秒）
# - 新增 _get_db_timeout() 带 5 秒 TTL 缓存，避免每次 get_db 都读 config.json
# - 用户改设置后最多 5 秒生效
# - config.json 损坏时回退 30 秒默认值
# - _get_db_timeout 定义在 cfg_get 之后（依赖在前，使用在后，风格规范）

import os
import json
import sqlite3
import logging
import re
import time  # 【⏱️ 延迟与超时精细控制】用于 _get_db_timeout 的 TTL 缓存

from netdisk_state_paths import CONFIG_FILE, DB_FILE
from netdisk_state_locks import CONFIG_LOCK


def get_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if "accounts" not in data: data["accounts"] = {}
                if "current" not in data: data["current"] = ""
                if "proxies" not in data: data["proxies"] = []
                return data
        except: pass
    return {"current": "", "accounts": {}, "proxies": []}


def save_config(cfg):
    with CONFIG_LOCK:
        tmp_file = CONFIG_FILE + ".tmp"
        with open(tmp_file, 'w', encoding='utf-8') as f: json.dump(cfg, f, ensure_ascii=False, indent=4)
        os.replace(tmp_file, CONFIG_FILE)


def cfg_get(key, default):
    try:
        v = get_config().get(key, default)
        if isinstance(default, bool):
            if isinstance(v, str): return v.lower() in ('1', 'true', 'yes', 'on')
            return bool(v)
        if isinstance(default, int):
            try: return int(v)
            except: return default
        if isinstance(default, float):
            try: return float(v)
            except: return default
        return v if v is not None else default
    except:
        return default


# ========== 【⏱️ 延迟与超时精细控制】db_timeout 缓存 ==========
# 用 5 秒 TTL 缓存避免 get_db() 高频调用时每次都读 config.json。
# 缓存命中时不读文件；过期后读一次；config 损坏时回退默认 30 秒。
_DB_TIMEOUT_CACHE = {'value': 30, 'at': 0.0}
_DB_TIMEOUT_TTL = 5.0


def _get_db_timeout():
    """【⏱️ 延迟与超时精细控制 - 2026-11】读取 db_timeout 配置（带 5 秒 TTL 缓存）。

    - 默认 30 秒，范围 5-300 秒（与 settings 页的 min/max 一致）
    - 5 秒内多次调用只读一次 config.json（性能优化）
    - 非法值 / config 损坏 → 回退 30 秒默认
    """
    now = time.time()
    if now - _DB_TIMEOUT_CACHE['at'] < _DB_TIMEOUT_TTL:
        return _DB_TIMEOUT_CACHE['value']
    try:
        v = max(5, min(int(cfg_get('db_timeout', 30)), 300))
    except Exception:
        v = 30
    _DB_TIMEOUT_CACHE['value'] = v
    _DB_TIMEOUT_CACHE['at'] = now
    return v


def get_db():
    # 【⏱️ 延迟与超时精细控制】timeout 从硬编码 30.0 改为读 db_timeout 配置
    conn = sqlite3.connect(DB_FILE, timeout=float(_get_db_timeout()))
    conn.execute("PRAGMA journal_mode=WAL;")
    def regexp(expr, item):
        if item is None: return False
        try: return re.search(expr, str(item)) is not None
        except: return False
    conn.create_function("REGEXP", 2, regexp)
    return conn


def _safe_execute(cursor, sql, params=None, label=""):
    """安全执行单条 SQL，失败仅 log 不抛异常。

    用于 init_db 的断电恢复阶段，避免一步失败影响后续步骤。

    【🐛 修复】if params is not None（原来是 if params:，
    空列表 [] 为 falsy 会走无参版本导致 sqlite3.ProgrammingError）
    """
    try:
        if params is not None:
            cursor.execute(sql, params)
        else:
            cursor.execute(sql)
        return True
    except Exception as e:
        logging.error(f"[init_db] {label or 'SQL'} 失败: {e}")
        return False


def init_db():
    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor()

        # ========== 建表（这部分失败是致命的，保持原有 try/except） ==========
        cursor.execute('''CREATE TABLE IF NOT EXISTS folders (id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT, name TEXT NOT NULL, parent_id INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT, folder_id INTEGER DEFAULT 0, identifier TEXT NOT NULL, title TEXT NOT NULL, creator TEXT, description TEXT, date TEXT, mediatype TEXT, collection TEXT, language TEXT, license TEXT, tags TEXT, file_path TEXT, file_name TEXT, status TEXT DEFAULT 'pending', item_url TEXT, direct_url TEXT, torrent_url TEXT, error_msg TEXT, deleted_at TIMESTAMP DEFAULT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, custom_date TEXT, magnet TEXT, md5 TEXT, UNIQUE(owner, identifier))''')
        for col in ['custom_date', 'magnet', 'md5']:
            try: cursor.execute(f"ALTER TABLE items ADD COLUMN {col} TEXT")
            except sqlite3.OperationalError: pass
        cursor.execute('''CREATE TABLE IF NOT EXISTS file_cache (id INTEGER PRIMARY KEY AUTOINCREMENT, identifier TEXT NOT NULL, filename TEXT NOT NULL, size TEXT, md5 TEXT, torrent_filename TEXT, custom_direct_url TEXT, cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, UNIQUE(identifier, filename))''')
        try: cursor.execute("ALTER TABLE file_cache ADD COLUMN md5 TEXT")
        except sqlite3.OperationalError: pass

        cursor.execute('''CREATE TABLE IF NOT EXISTS upload_tasks (task_id TEXT PRIMARY KEY, title TEXT, status TEXT, msg TEXT, percent INTEGER, speed TEXT, current TEXT, total TEXT, eta TEXT, raw_line TEXT, batch_total INTEGER, batch_done INTEGER, task_type TEXT, extra_data TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        for col in ['task_type', 'extra_data']:
            try: cursor.execute(f"ALTER TABLE upload_tasks ADD COLUMN {col} TEXT")
            except sqlite3.OperationalError: pass

        cursor.execute('''CREATE TABLE IF NOT EXISTS sub_tasks (task_id TEXT, file_path TEXT, file_size INTEGER, status TEXT DEFAULT 'waiting', uploaded_bytes INTEGER DEFAULT 0, error_msg TEXT, PRIMARY KEY (task_id, file_path))''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS file_chunks (identifier TEXT, file_path TEXT, upload_id TEXT, part_number INTEGER, etag TEXT, status TEXT DEFAULT 'pending', PRIMARY KEY (identifier, file_path, part_number))''')

        conn.commit()

        # ========== 断电恢复（每步独立 try/except） ==========
        # 1. items：上次运行时 uploading → error（保留临时文件，用户重试可断点续传）
        _safe_execute(
            cursor,
            "UPDATE items SET status='error', error_msg='上次运行意外中断，临时文件已保留，请点击重试以断点续传。' WHERE status='uploading'",
            label="items.uploading→error"
        )

        # 2. items：上次运行时 meta_pending → error
        _safe_execute(
            cursor,
            "UPDATE items SET status='error', error_msg='上次运行意外中断，请点击重试以重推元数据。' WHERE status='meta_pending'",
            label="items.meta_pending→error"
        )

        # 3. sub_tasks：上次运行时 uploading → error
        _safe_execute(
            cursor,
            "UPDATE sub_tasks SET status='error', "
            "error_msg='上次运行意外中断，已上传分片已保留，点击重试可断点续传' "
            "WHERE status='uploading'",
            label="sub_tasks.uploading→error"
        )

        # 4. upload_tasks：清理已完成任务
        _safe_execute(
            cursor,
            "DELETE FROM upload_tasks WHERE status = 'done'",
            label="clean done upload_tasks"
        )

        # 5. upload_tasks：清理浏览器下载虚拟任务
        _safe_execute(
            cursor,
            "DELETE FROM upload_tasks WHERE task_type = 'download_browser'",
            label="clean download_browser upload_tasks"
        )

        # 6. upload_tasks：断电后，把有断点恢复机制的任务置为 error
        _safe_execute(
            cursor,
            "UPDATE upload_tasks SET status='error', "
            "msg='系统重启导致任务中断，点击「重试」可继续断点续传' "
            "WHERE status IN ('waiting', 'downloading', 'uploading', 'deleting', 'paused') "
            "AND task_type IN ('download', 'download_folder', 'delete', 'append', 'rename', 'pull', 'push_meta')",
            label="upload_tasks.非upload任务→error"
        )

        # 7. upload_tasks：upload 任务的恢复不依赖 upload_tasks 表（通过 items 表和前端重试队列恢复）
        # 【G-50 修复】直接删除所有 task_type='upload' 的记录（包括 status='error'）
        # 原因：upload_worker 的恢复逻辑是：
        #   - items 表 status='error' → 用户在待重试队列看到 → 点「重试」→ 重新起 upload_worker
        #   - upload_tasks 表对 upload 类型任务不参与恢复
        # 之前只删了 waiting/uploading/paused/downloading/deleting，
        # status='error' 的 upload 任务会无限累积
        _safe_execute(
            cursor,
            "DELETE FROM upload_tasks WHERE task_type = 'upload'",
            label="clean all upload task upload_tasks"
        )

        conn.commit()
    except Exception as e:
        logging.error(f"init_db 失败: {e}")
    finally:
        if conn is not None:
            try:
                conn.close()
            except:
                pass


# 模块加载时执行一次（与原始 netdisk_state_db.py 行为一致）
init_db()


def get_active_account():
    cfg = get_config()
    current = cfg.get("current")
    if current and current in cfg.get("accounts", {}): return cfg["accounts"][current]
    return None


def get_max_concurrent():
    try: return max(1, min(int(cfg_get('max_concurrent', 1)), 10))
    except: return 1


def get_folder_tree_options(owner):
    # 【G-70】conn.close() 加 try/finally，防 conn.execute 抛异常时 conn 泄漏
    conn = get_db()
    try:
        folders = conn.execute("SELECT id, name, parent_id FROM folders WHERE owner=? ORDER BY name", (owner,)).fetchall()
    finally:
        conn.close()
    tree = {}
    for fid, name, pid in folders:
        if pid not in tree: tree[pid] = []
        tree[pid].append((fid, name))
    options = []
    def build_tree(parent_id, depth):
        if parent_id not in tree: return
        for fid, name in tree[parent_id]:
            prefix = "　" * depth
            if depth > 0: prefix += "└ "
            options.append((fid, f"{prefix}{name}"))
            build_tree(fid, depth + 1)
    build_tree(0, 0)
    return options