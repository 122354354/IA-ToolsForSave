# netdisk_routes_views_settings.py
# 设置页 / 临时清理 / 备份导出恢复
# 【改进点 76 - 2/2】保存 + 读取 raw_line_max_lines 设置项
#
# 【⏱️ 延迟与超时精细控制 - 2026-11】
# 7 大板块全部配置项（55 项）通过 cfg_get 读取，默认值兜底。
#
# 【🔴 R273 修复 - 2026-11（本轮）】
#   warp_reg_request_timeout 默认值从 10 改为 15。
#   原因：原第 20b 个文件 warp_generate.py 的 WARP 设备注册请求有 2 个不同硬编码：
#     - get_available_proxy 临时节点：requests.post(timeout=10)
#     - start_custom_warp W 协议：requests.post(timeout=15)
#   第 7 次生成统一为 warp_reg_request_timeout 默认 10 → W 协议从 15 秒变 10 秒（用户可感知退步）。
#   修复：默认值改为 15 → W 协议与原完全一致；临时节点原 10 → 新 15（多等 5 秒，成功概率更高，无副作用）。
#
# 【🔴 R279 修复 - 2026-11（本轮）】
#   新增 id_check_ip_window（默认 60，10-3600 秒）。
#   原因：api_check_identifier 的 IP_ID_CHECK 60 秒滑动窗口仍硬编码，与已配置化的
#        proxy_dl_ip_window / thumb_ip_window 风格不一致。
#   修复：新增配置项，第 3 个文件 netdisk_routes_api_browse.py 下次接入。
#
# 【配置项总数 - 本轮后】
#   _NEW_SETTINGS_INT: 48 → 49（+id_check_ip_window）
#   _NEW_SETTINGS_FLOAT: 7（不变）
#   合计: 55 → 56
#
# 【🔴 R61 修复 - 2026-11（已完成）】
#   download_retry_sleep 默认值从 1.0 改为 5.0（与第 7 个文件代码默认值一致）
#
# 【🔴 B 级 6 项配置 - 2026-11（已完成）】
#   proxy_dl_ip_window / thumb_ip_window / id_check_timeout
#   warp_usque_startup_timeout / dns_check_timeout / warp_reg_request_timeout

import os
import json
import zipfile
from flask import request, render_template_string, redirect, url_for, send_file

from netdisk_blueprints import views_bp
from netdisk_core import (
    get_config, save_config, cfg_get,
    DB_LOCK, get_db, cleanup_temp_dir,
)
from templates_module import STYLE, get_navbar
from pages_module import SETTINGS_HTML

import netdisk_core as core


# ========== 【⏱️ 延迟与超时精细控制】新配置项定义 ==========
# 结构：(配置键, 默认值, 最小值, 最大值)
_NEW_SETTINGS_INT = [
    # ---- 板块1：⬆️ 上传与 S3 分片延迟 ----
    ('s3_retry_backoff_base', 2, 1, 10),
    ('s3_timeout_connect', 15, 5, 120),
    ('s3_timeout_read', 60, 10, 600),
    ('upload_file_retry', 5, 0, 20),
    ('tcp_keepidle', 30, 5, 300),
    ('tcp_keepintvl', 10, 1, 60),
    # ---- 板块2：⬇️ 下载与代理延迟 ----
    ('download_timeout_connect', 15, 5, 120),
    ('download_timeout_read', 15, 5, 300),
    ('stream_chunk_size', 65536, 4096, 1048576),
    ('proxy_dl_ip_limit', 200, 1, 10000),
    ('proxy_dl_ip_window', 60, 10, 3600),  # 【B 级新增】代理下载 IP 滑动窗口
    ('proxy_dl_timeout_connect', 5, 1, 60),
    ('proxy_dl_timeout_read', 15, 5, 300),
    ('proxy_dl_retry', 3, 0, 10),
    ('preview_timeout_connect', 10, 1, 60),
    ('preview_timeout_read', 60, 5, 600),
    # ---- 板块3：🌐 前端分片与轮询 ----
    ('frontend_chunk_threshold_mb', 50, 5, 2048),
    ('frontend_chunk_size_mb', 5, 1, 50),
    ('frontend_chunk_retries', 3, 0, 10),
    ('frontend_chunk_retry_sleep_ms', 800, 100, 10000),
    ('queue_poll_interval_ms', 500, 200, 10000),
    ('queue_refresh_timeout_ms', 10000, 2000, 60000),
    ('queue_stuck_timeout_ms', 15000, 3000, 120000),
    ('upload_xhr_timeout_ms', 600000, 30000, 3600000),
    ('detail_fetch_timeout_ms', 15000, 1000, 120000),
    ('detail_zip_timeout_ms', 30000, 3000, 300000),
    ('id_check_timeout', 20000, 5000, 120000),  # 【B 级新增】ID 预检超时（毫秒）
    ('id_check_ip_window', 60, 10, 3600),  # 【R279 新增】ID 预检 IP 滑动窗口（秒）
    # ---- 板块4：🖼️ 缩略图与虚拟任务 ----
    ('thumb_timeout', 4, 1, 60),
    ('thumb_manual_timeout', 15, 3, 120),
    ('thumb_fail_cache_ttl', 300, 0, 3600),
    ('thumb_ip_window', 60, 10, 3600),  # 【B 级新增】缩略图 IP 滑动窗口
    ('virtual_task_ttl', 0, 0, 86400),
    # ---- 板块5：📋 任务队列与数据库 ----
    ('max_auto_restart_per_resume', 20, 1, 100),
    ('db_timeout', 30, 5, 300),
    ('worker_max_retries', 5, 0, 20),
    # ---- 板块6：🤖 后台守护（需先开启对应开关才生效） ----
    ('auto_backup_days', 7, 1, 365),
    ('auto_backup_keep', 4, 1, 20),
    ('clean_memory_interval', 3600, 60, 86400),
    ('clean_memory_age', 3600, 60, 86400),
    # ---- 板块7：🌐 WARP 代理 ----
    ('warp_ip_concurrency', 20, 1, 100),
    ('warp_ip_keep', 5, 1, 20),
    ('warp_register_timeout', 120, 30, 600),
    ('warp_direct_timeout', 30, 10, 300),
    ('warp_probe_timeout', 10, 3, 60),
    ('proxy_test_timeout', 8, 1, 60),
    ('warp_usque_startup_timeout', 15, 5, 120),
    ('dns_check_timeout', 5, 1, 60),
    # 【R273 修复 - 2026-11】warp_reg_request_timeout 默认值 10 → 15
    #   原因：原 W 协议注册超时 15 秒，第 7 次生成统一后默认 10 → 慢网络会失败
    #   修复：默认 15 → W 协议与原完全一致；临时节点原 10 → 15（多等 5 秒，无副作用）
    ('warp_reg_request_timeout', 15, 5, 60),
    # 注：warp_startup_wait 是 float，在 _NEW_SETTINGS_FLOAT 表
]

_NEW_SETTINGS_FLOAT = [
    # ---- 板块1：⬆️ 上传与 S3 分片延迟 ----
    ('s3_part_sleep', 0.1, 0.0, 10.0),
    ('upload_poll_interval', 0.5, 0.1, 10.0),
    # ---- 板块2：⬇️ 下载与代理延迟 ----
    # 【R61 修复 - 2026-11】download_retry_sleep 默认值 1.0 → 5.0
    ('download_retry_sleep', 5.0, 0.0, 60.0),
    ('download_progress_interval', 1.0, 0.1, 10.0),
    # ---- 板块7：🌐 WARP 代理 ----
    ('warp_ip_timeout', 1.5, 0.5, 10.0),
    ('warp_ip_threshold', 1.5, 0.5, 10.0),
    ('warp_startup_wait', 1.0, 0.3, 10.0),
]


def _load_new_settings_to_cfg(cfg, form):
    """【⏱️ 延迟与超时精细控制】保存全部新配置项到 cfg。

    优先级（从高到低）：
      1. 表单提交的合法值
      2. 旧配置（clamp 后）
      3. hardcode 默认值（仅在旧配置也损坏时）
    """
    for key, default, lo, hi in _NEW_SETTINGS_INT:
        try:
            fallback_val = max(lo, min(int(cfg.get(key, default)), hi))
        except Exception:
            fallback_val = default
        try:
            cfg[key] = max(lo, min(int(form.get(key, fallback_val)), hi))
        except Exception:
            cfg[key] = fallback_val

    for key, default, lo, hi in _NEW_SETTINGS_FLOAT:
        try:
            fallback_val = max(lo, min(float(cfg.get(key, default)), hi))
        except Exception:
            fallback_val = default
        try:
            cfg[key] = max(lo, min(float(form.get(key, fallback_val)), hi))
        except Exception:
            cfg[key] = fallback_val


@views_bp.route('/settings', methods=['GET', 'POST'])
def settings_view():
    cfg = get_config()
    if request.method == 'POST':
        action = request.form.get('action', 'save')
        if action == 'save':
            try:
                n = max(1, min(int(request.form.get('max_concurrent', 1)), 10))
            except:
                n = 1
            cfg['max_concurrent'] = n

            try:
                cfg['delete_concurrency'] = max(1, min(int(request.form.get('delete_concurrency', 1)), 10))
            except:
                cfg['delete_concurrency'] = 1

            cfg['verify_ssl'] = request.form.get('verify_ssl') == 'yes'

            cfg['local_pwd'] = request.form.get('local_pwd', '').strip()
            cfg['cdn_prefix'] = request.form.get('cdn_prefix', '').strip()
            cfg['proxy'] = request.form.get('proxy', '').strip()
            cfg['download_dir'] = request.form.get('download_dir', '').strip() or '/sdcard/Download'
            try:
                cfg['trash_retention'] = max(0, int(request.form.get('trash_retention', 0)))
            except:
                cfg['trash_retention'] = 0
            for k in ['auto_fetch_on_detail', 'auto_load_thumb', 'auto_check_id', 'auto_backup', 'auto_clean_trash', 'default_generate_torrent', 'auto_clean_tasks', 'download_via_proxy', 'auto_clear_history']:
                cfg[k] = request.form.get(k) == 'yes'
            af = request.form.get('auto_fetch_after_upload', 'lazy')
            if af in ('lazy', 'immediate', 'never'):
                cfg['auto_fetch_after_upload'] = af
            sdd = request.form.get('single_download_default', 'ask')
            if sdd in ('ask', 'termux', 'browser'):
                cfg['single_download_default'] = sdd
            bdd = request.form.get('batch_download_default', 'ask')
            if bdd in ('ask', 'termux', 'browser', 'per_file'):
                cfg['batch_download_default'] = bdd
            for k, default, lo, hi in [('ia_rate_max', 10, 1, 100), ('ia_rate_window', 60, 5, 600), ('ia_timeout', 30, 5, 300), ('ia_429_backoff', 60, 10, 600), ('ia_429_max_retries', 1, 0, 5)]:
                try:
                    cfg[k] = max(lo, min(int(request.form.get(k, default)), hi))
                except:
                    cfg[k] = default
            for k, default in [('file_cache_ttl', -1), ('id_check_ttl', 300), ('magnet_fail_ttl', 300), ('pull_cache_ttl', -1), ('thumb_cache_ttl', -1)]:
                try:
                    cfg[k] = max(-1, int(request.form.get(k, default)))
                except:
                    cfg[k] = default
            for k, default, lo, hi in [('id_check_debounce_ms', 800, 0, 5000), ('id_check_ip_limit', 6, 1, 100), ('thumb_ip_limit', 30, 1, 500), ('refresh_cooldown', 10, 0, 3600)]:
                try:
                    cfg[k] = max(lo, min(int(request.form.get(k, default)), hi))
                except:
                    cfg[k] = default
            try:
                cfg['thumb_download_sleep'] = max(0.0, float(request.form.get('thumb_download_sleep', 0.2)))
            except:
                cfg['thumb_download_sleep'] = 0.2
            for k, default, lo, hi in [('pull_page_size', 100, 10, 500), ('pull_max_pages', 50, 1, 200)]:
                try:
                    cfg[k] = max(lo, min(int(request.form.get(k, default)), hi))
                except:
                    cfg[k] = default
            try:
                cfg['pull_page_sleep'] = max(0.0, float(request.form.get('pull_page_sleep', 0.5)))
            except:
                cfg['pull_page_sleep'] = 0.5
            for k, default, lo, hi in [('upload_retries_max', 3, 0, 5), ('upload_sleep_min', 10, 5, 300), ('upload_post_sleep', 15, 0, 300), ('execute_batch_limit', 10, 1, 100), ('raw_line_max_lines', 500, 10, 5000)]:
                try:
                    cfg[k] = max(lo, min(int(request.form.get(k, default)), hi))
                except:
                    cfg[k] = default
            dp = request.form.get('derive_priority', 'no_derive')
            if dp in ('generate_torrent', 'no_derive', 'both', 'auto'):
                cfg['derive_priority'] = dp
            try:
                cfg['delete_file_sleep'] = max(0.0, float(request.form.get('delete_file_sleep', 2.0)))
            except:
                cfg['delete_file_sleep'] = 2.0
            try:
                cfg['append_post_sleep'] = max(0.0, float(request.form.get('append_post_sleep', 10)))
            except:
                cfg['append_post_sleep'] = 10
            try:
                cfg['magnet_timeout'] = max(5, int(request.form.get('magnet_timeout', 30)))
            except:
                cfg['magnet_timeout'] = 30
            try:
                cfg['magnet_concurrent_limit'] = max(1, min(10, int(request.form.get('magnet_concurrent_limit', 2))))
            except:
                cfg['magnet_concurrent_limit'] = 2
            cfg['rename_allow_tasks_php'] = request.form.get('rename_allow_tasks_php') == 'yes'
            cfg['rename_show_official_hint'] = request.form.get('rename_show_official_hint') == 'yes'
            try:
                cfg['rename_cooldown'] = max(0, int(request.form.get('rename_cooldown', 300)))
            except:
                cfg['rename_cooldown'] = 300
            try:
                cfg['rename_account_hourly_limit'] = max(1, int(request.form.get('rename_account_hourly_limit', 5)))
            except:
                cfg['rename_account_hourly_limit'] = 5

            # ========== 【⏱️ 延迟与超时精细控制】7 大板块新配置保存 ==========
            _load_new_settings_to_cfg(cfg, request.form)

            save_config(cfg)
            core.CONCURRENCY_LIMITER.set_max(n)
            return redirect(url_for('views.settings_view', saved='1'))

    temp_size = 0
    temp_count = 0
    if os.path.exists(core.TEMP_UPLOAD_DIR):
        for name in os.listdir(core.TEMP_UPLOAD_DIR):
            p = os.path.join(core.TEMP_UPLOAD_DIR, name)
            try:
                if os.path.isdir(p):
                    for root, _, fs in os.walk(p):
                        for f in fs:
                            temp_size += os.path.getsize(os.path.join(root, f))
                else:
                    temp_size += os.path.getsize(p)
                temp_count += 1
            except: pass
    def fmt_size(b):
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if b < 1024:
                return f"{b:.1f} {unit}"
            b /= 1024
        return f"{b:.1f} PB"

    # 【⏱️ 延迟与超时精细控制】渲染 cfg dict 时追加全部新配置键
    _base_cfg_pairs = [
        ('ia_rate_max', 10), ('ia_rate_window', 60), ('ia_timeout', 30),
        ('ia_429_backoff', 60), ('ia_429_max_retries', 1),
        ('file_cache_ttl', -1), ('id_check_ttl', 300), ('magnet_fail_ttl', 300),
        ('pull_cache_ttl', -1), ('thumb_cache_ttl', -1),
        ('id_check_debounce_ms', 800), ('id_check_ip_limit', 6), ('thumb_ip_limit', 30),
        ('refresh_cooldown', 10), ('thumb_download_sleep', 0.2),
        ('pull_page_size', 100), ('pull_max_pages', 50), ('pull_page_sleep', 0.5),
        ('upload_retries_max', 3), ('upload_sleep_min', 10), ('upload_post_sleep', 15),
        ('execute_batch_limit', 10), ('derive_priority', 'no_derive'),
        ('raw_line_max_lines', 500),
        ('delete_file_sleep', 2.0), ('append_post_sleep', 10),
        ('magnet_timeout', 30), ('magnet_concurrent_limit', 2),
        ('rename_allow_tasks_php', False), ('rename_show_official_hint', True),
        ('rename_cooldown', 300), ('rename_account_hourly_limit', 5),
        ('auto_fetch_after_upload', 'lazy'), ('auto_fetch_on_detail', False),
        ('auto_load_thumb', False), ('auto_check_id', False),
        ('auto_backup', False), ('auto_clean_trash', False),
        ('auto_clean_tasks', False), ('download_via_proxy', True),
        ('auto_clear_history', False), ('verify_ssl', True),
    ]
    _new_cfg_pairs = [(k, d) for k, d, _lo, _hi in _NEW_SETTINGS_INT] + \
                     [(k, d) for k, d, _lo, _hi in _NEW_SETTINGS_FLOAT]
    _all_cfg_pairs = _base_cfg_pairs + _new_cfg_pairs

    return render_template_string(
        SETTINGS_HTML, style=STYLE,
        navbar=get_navbar(False, active_page='settings'),
        max_c=cfg.get('max_concurrent', 1),
        delete_conc=cfg.get('delete_concurrency', 1),
        local_pwd=cfg.get('local_pwd', ''),
        cdn_prefix=cfg.get('cdn_prefix', ''),
        proxy=cfg.get('proxy', ''),
        dl_dir=cfg.get('download_dir', '/sdcard/Download'),
        trash_ret=cfg_get('trash_retention', 0),
        temp_dir=core.TEMP_UPLOAD_DIR,
        temp_size=fmt_size(temp_size), temp_count=temp_count,
        single_download_default=cfg_get('single_download_default', 'ask'),
        batch_download_default=cfg_get('batch_download_default', 'ask'),
        cfg={k: cfg_get(k, d) for k, d in _all_cfg_pairs}
    )


@views_bp.route('/settings/clean_temp', methods=['POST'])
def clean_temp():
    try:
        if os.path.exists(core.TEMP_UPLOAD_DIR):
            for name in os.listdir(core.TEMP_UPLOAD_DIR):
                cleanup_temp_dir(os.path.join(core.TEMP_UPLOAD_DIR, name))
        return redirect(url_for('views.settings_view', cleaned='1'))
    except Exception as e:
        return f"❌ 清理失败: {e}"


@views_bp.route('/backup/download')
def backup_download():
    try:
        with DB_LOCK:
            conn = get_db()
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            conn.close()

        backup_path = os.path.join(core.HOME_DIR, "netdisk_backup.zip")
        with zipfile.ZipFile(backup_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            if os.path.exists(core.CONFIG_FILE):
                zf.write(core.CONFIG_FILE, 'config.json')
            if os.path.exists(core.DB_FILE):
                zf.write(core.DB_FILE, 'netdisk.db')
        return send_file(backup_path, as_attachment=True, download_name="netdisk_backup.zip")
    except Exception as e:
        return f"❌ 备份失败: {e}"


@views_bp.route('/backup/upload', methods=['POST'])
def backup_upload():
    if 'file' not in request.files:
        return "❌ 未选择文件"
    file = request.files['file']
    if file and file.filename.endswith('.zip'):
        try:
            temp_zip = os.path.join(core.HOME_DIR, "upload_backup.zip")
            file.save(temp_zip)

            for suffix in ['', '-wal', '-shm']:
                p = core.DB_FILE + suffix
                if os.path.exists(p):
                    try:
                        os.remove(p)
                    except Exception as e:
                        os.remove(temp_zip)
                        return f"❌ 无法清理旧数据库文件，请完全关闭程序后再试: {e}"

            with zipfile.ZipFile(temp_zip, 'r') as zf:
                names = zf.namelist()
                if 'config.json' in names:
                    try:
                        with zf.open('config.json') as f:
                            json.loads(f.read().decode('utf-8'))
                        zf.extract('config.json', core.HOME_DIR)
                    except Exception as e:
                        os.remove(temp_zip)
                        return f"❌ config.json 格式损坏: {e}"
                if 'netdisk.db' in names:
                    zf.extract('netdisk.db', core.HOME_DIR)
            os.remove(temp_zip)
            return "<script>alert('✅ 备份恢复成功！请手动重启程序生效。');location.href='/settings';</script>"
        except Exception as e:
            return f"❌ 恢复失败: {e}"
    return "❌ 格式错误，请上传 .zip 文件"