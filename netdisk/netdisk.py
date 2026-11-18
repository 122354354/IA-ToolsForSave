# netdisk.py
import os
import sys
import logging
import subprocess
import threading
import time
import sqlite3

# 导入 Flask 实例
from netdisk_app import app, _clean_virtual_tasks_worker
# 导入拆分出去的蓝图
from netdisk_routes_api import api_bp
from netdisk_routes_views import views_bp

import netdisk_core as core
from netdisk_core import UPLOAD_TASKS, DB_LOCK, get_db
import warp_module

# 注册蓝图 (Blueprint) 到主应用
app.register_blueprint(api_bp)
app.register_blueprint(views_bp)


def _setup_console_logging():
    """让所有 logging 输出同时显示在终端（stderr），解决"启动无任何输出"问题"""
    root = logging.getLogger()
    # 避免重复添加
    for h in root.handlers:
        if getattr(h, 'is_console', False):
            return
    sh = logging.StreamHandler(sys.stderr)
    sh.setLevel(logging.INFO)
    sh.setFormatter(logging.Formatter('[%(levelname)s] %(message)s'))
    sh.is_console = True
    root.addHandler(sh)
    root.setLevel(logging.INFO)


def _check_port_available(port):
    """启动前检查端口是否被占用"""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(('0.0.0.0', port))
            return True
        except OSError:
            return False


def _restore_interrupted_tasks():
    logging.info("🔄 正在恢复中断的下载/删除任务...")
    try:
        with DB_LOCK:
            conn = get_db()
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM upload_tasks WHERE status = 'error'").fetchall()
            conn.close()
        for r in rows:
            tid = r['task_id']
            if tid in UPLOAD_TASKS: continue
            task_type = r['task_type']
            extra = r['extra_data']
            if not extra or not task_type or task_type in ('upload', 'download_browser'):
                continue

            UPLOAD_TASKS[tid] = {
                'title': r['title'], 'status': 'error', 'msg': r['msg'], 'percent': r['percent'], 'speed': r['speed'],
                'current': r['current'], 'total': r['total'], 'eta': r['eta'], 'raw_line': r['raw_line'],
                'batch_total': r['batch_total'], 'batch_done': r['batch_done'], 'task_type': task_type,
                'extra_data': extra, 'created_at': time.time()
            }
            logging.info(f"✅ 恢复了断点任务: {r['title']}")
    except Exception as e:
        logging.error(f"恢复任务失败: {e}")


if __name__ == '__main__':
    # 第一步：让 logging 显示在终端（必须在其他日志之前）
    _setup_console_logging()

    print("=" * 60)
    print("🚀 IA 网盘 V135 启动中...")
    print("=" * 60)

    logging.info("🚀 启动 IA 网盘 (V135 - 终极模块化清爽版)")

    # 尝试获取 Termux 防休眠锁
    try:
        subprocess.run(["termux-wake-lock"], timeout=2)
        logging.info("🔒 已成功获取 Termux Wake Lock (防休眠)")
    except:
        logging.info("ℹ️ 非 Termux 环境或 termux-api 未安装，跳过防休眠锁")

    # 自动拉起已保存的代理节点
    logging.info("🌐 检查已保存的代理节点...")
    try:
        warp_module.autostart_saved_proxies()
        logging.info("✅ 代理节点检查完毕")
    except Exception as e:
        logging.error(f"⚠️ 代理节点自启动失败（不影响主服务）: {e}")

    # 启动后台守护线程
    logging.info("⚙️ 启动后台守护线程...")
    threading.Thread(target=core._auto_backup_worker, daemon=True).start()
    threading.Thread(target=core._auto_clean_trash_worker, daemon=True).start()
    threading.Thread(target=core._clean_memory_tasks_worker, daemon=True).start()
    threading.Thread(target=_clean_virtual_tasks_worker, daemon=True).start()
    logging.info("✅ 后台守护线程已启动")

    # 恢复上次异常中断的断点任务
    _restore_interrupted_tasks()

    # 端口占用检测（防止无限静默重试）
    if not _check_port_available(5000):
        logging.error("❌ 端口 5000 已被占用！")
        logging.error("   可能是之前启动的网盘进程还没关闭。")
        logging.error("   请在另一个终端执行以下命令之一来清理：")
        logging.error("     pkill -f 'python netdisk.py'")
        logging.error("     fuser -k 5000/tcp   # 需要 root")
        logging.error("   然后重新启动本程序。")
        print("\n❌ 端口 5000 已被占用，程序退出。\n")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("✅ 准备就绪，正在启动 Flask Web 服务...")
    print("   监听地址: http://0.0.0.0:5000")
    print("   手机端访问: 在浏览器打开你手机的局域网 IP:5000")
    print("   本机访问: http://127.0.0.1:5000")
    print("   按 Ctrl+C 退出")
    print("=" * 60 + "\n")

    # 启动 Flask Web 服务（保留自动重试，但会明确打印异常）
    retry_count = 0
    while True:
        try:
            app.run(host='0.0.0.0', port=5000, threaded=True, use_reloader=False)
            # 正常返回只会在收到 SIGINT/SIGTERM 时发生
            logging.info("ℹ️ Flask 服务已停止（收到退出信号）")
            break
        except KeyboardInterrupt:
            logging.info("ℹ️ 收到 Ctrl+C，正在退出...")
            break
        except Exception as e:
            retry_count += 1
            logging.error(f"❌ 系统崩溃（第 {retry_count} 次重试），3 秒后自动重启: {e}")
            import traceback
            traceback.print_exc()  # 直接打印 traceback 到 stderr
            time.sleep(3)
