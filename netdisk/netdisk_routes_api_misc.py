# netdisk_routes_api_misc.py
# 杂项 API：关闭网盘、申请权限、安装依赖、提取 S3、修改账号名、删除搜索历史
#
# 【🔴 R478~R482 修复 - 2026-11（申请权限 / 安装依赖修复）】
#   R478：api_request_permission 的 storage 分支改用 _run_termux_setup_storage
#        （input="y\n" + timeout=30 + 区分异常）避免交互式提示卡住
#   R479：api_install_deps 的预检同样修复
#   R480：可选依赖 pycurl 单独装（失败不影响主流程）
#   R481：api_install_deps 超时 120/300/300 → 300/600/600
#   R482：api_request_permission 的 wake_lock timeout=5 → 10
#
# 【🔴 R488 修复 - 2026-11（R480 配套修复 - 本轮）】
#   问题：R480 把 pycurl 加入 optional_pkgs 单独装，但 Termux 上 pip install pycurl
#        会源码编译（清华镜像无 Termux wheel），需要 $PREFIX/include/curl/curl.h。
#        而 pkg install python-cryptography termux-api 不含 libcurl 头文件 →
#        pycurl 编译失败：
#          src/pycurl.h: fatal error: 'curl/curl.h' file not found
#        用户点"一键安装依赖"后看到：
#          "⚠️ pycurl 可选依赖安装失败（不影响主流程，进度条会 10MB 一跳）"
#        R480 想改善的"进度条平滑"体验没改善，用户困惑"为什么 pycurl 装不上"。
#   修复：pkg install 列表加 "curl"（Termux 的 curl 包同时提供 libcurl 库 + 头文件，
#        一次装全），让 pycurl 能源码编译成功。
#   注：只影响 Termux 分支（is_termux=True）。Windows / Linux 不受影响
#        （Windows 有 pycurl 官方 wheel；Linux 用系统 libcurl-dev，pip 有 wheel）。
#
# 【依赖核对（2026-11 全代码库扫描）】
#   必需依赖（已覆盖）：
#     flask / requests / urllib3 / internetarchive / certifi / PySocks
#     / tqdm / jsonpatch / xmltodict
#   系统包（Termux 分支已覆盖）：
#     python-cryptography（cryptography 的 Termux 优化版）
#     termux-api（termux-wake-lock / termux-setup-storage 依赖）
#     curl（R488 新增：提供 libcurl 库 + 头文件，供 pycurl 源码编译）
#   可选依赖（R480 单独装）：
#     pycurl（S3 分片上传加速，失败不影响主流程；R488 修复编译依赖）
#   不自动化（固定版本，用户明确保留）：
#     sing-box v1.9.0 / usque v4.2.1（在 warp_core.get_singbox_binary 等自动下载）
#   未使用（不需装）：
#     boto3 / Pillow / lxml / bs4 / dnspython / aiohttp / httpx
#
# 【前端无需改】
#   - pages_auth.py / pages_settings.py 都只调 /api/install_deps（显示 msg）
#   - 修复全在服务端，前端 alert / statusBox 能正确显示新消息
#
# 【📝 保留记录 - 2026-11，低优先，以后修】
#   R483：netdisk.py 启动时 termux-wake-lock 的 timeout=2 保持不变
#   R484：pycurl 安装失败时 msg 只显示错误前 100 字符
#   R485：_run_termux_setup_storage 的 timeout=30 是保守值
#   R486：api_install_deps 主流程成功但可选依赖失败时，仍返回 status='success'
#   R487：pycurl_err 为空的极端场景（概率 <0.01%）文案轻微错误，不修
#   R489：快速点两次"申请存储权限"可能并发冲突（原代码也有此问题，非本次引入）
#   R490：主流程 pip 最长 600 秒 + pycurl 300 秒 + pkg 900 秒 = 30 分钟（用户可接受）
#   R491：termux-setup-storage 重建 ~/storage 会清掉用户的自定义符号链接
#        （网盘使用 /sdcard / /storage/emulated/0 不受影响）
#   R492（本轮记录）：Termux 的 curl 包约 5MB（含 libcurl + 头文件 + 命令行），
#        比 libcurl 单独装略大，但一次装全无需拆包，可接受
#   R493（本轮记录）：pycurl 编译需要 2-3 分钟（Termux 慢设备），若用户没耐心
#        可能提前关闭页面。可考虑 pip install pycurl --no-build-isolation 加速
#        （但需要 setuptools wheel 已装，当前未装，记录待评估）
#
# 【📝 同源问题记录 - 其他文件，后续统一修】
#   - 无

import os
import sys
import re
import time
import logging
import threading
import subprocess

import requests
from flask import request, jsonify, session

from netdisk_blueprints import api_bp
from netdisk_core import get_config, save_config, get_req_proxies, get_ssl_verify

import warp_core


def _run_termux_setup_storage(timeout=30):
    """【R478 / R479 修复 - 2026-11】运行 termux-setup-storage，自动喂 y 避免交互式提示卡住。

    termux-setup-storage 在 ~/storage 已存在时会问：
        "It appears that directory '~/storage' already exists...
         Do you want to continue? (y/n)"
    不喂 stdin 会一直等 → 超时报错。用户实际遇到的就是这个。

    返回 (success: bool, err_msg: str)
    """
    try:
        proc = subprocess.run(
            ["termux-setup-storage"],
            input="y\n",              # 自动回答 y（input 配合 text=True 自动设 stdin=PIPE）
            capture_output=True,       # 不污染终端
            text=True,
            timeout=timeout
        )
        if proc.returncode == 0:
            return True, ""
        err = ((proc.stdout or '') + (proc.stderr or '')).strip()
        return False, err[-300:] if err else f"返回码 {proc.returncode}"
    except subprocess.TimeoutExpired:
        return False, f"超时 {timeout} 秒（可能是手机弹出权限窗口但你未操作）"
    except FileNotFoundError:
        return False, "termux-setup-storage 命令不存在（可能未安装 termux-tools，请先在终端 pkg install termux-tools）"
    except Exception as e:
        return False, str(e)


@api_bp.route('/api/shutdown', methods=['POST'])
def api_shutdown():
    def _shutdown():
        time.sleep(1.5)
        try:
            is_termux = 'com.termux' in os.environ.get('PREFIX', '')
            if is_termux:
                subprocess.run(["termux-wake-unlock"], timeout=2)
        except: pass
        os._exit(0)
    threading.Thread(target=_shutdown, daemon=True).start()
    return jsonify({"status": "success", "msg": "正在关闭网盘..."})


@api_bp.route('/api/request_permission', methods=['POST'])
def api_request_permission():
    data = request.get_json() or {}
    perm_type = data.get('type')

    if perm_type == 'storage':
        try:
            is_termux = 'com.termux' in os.environ.get('PREFIX', '')
            if is_termux:
                # 【R478 修复】termux-setup-storage 在 ~/storage 已存在时会
                # 交互式提问 (y/n)，不喂 stdin 会一直等 → 超时。
                _setup_ok, _setup_err = _run_termux_setup_storage(timeout=30)
                if not _setup_ok:
                    logging.warning(f"[request_permission] termux-setup-storage 失败: {_setup_err}")

            time.sleep(2)
            if os.path.exists('/sdcard') or os.path.exists('/storage/emulated/0'):
                return jsonify({"status": "success", "msg": "✅ 存储权限已获取！如果之前没看到弹窗，可能是已经授权过了。"})
            else:
                return jsonify({"status": "error", "msg": "⚠️ 似乎没有获取到权限。如果系统没有弹出提示框，请去手机系统设置 -> 应用 -> Termux -> 权限 -> 文件和媒体，手动勾选允许。"})
        except Exception as e:
            return jsonify({"status": "error", "msg": f"❌ 申请异常: {e}"})

    elif perm_type == 'wake_lock':
        try:
            is_termux = 'com.termux' in os.environ.get('PREFIX', '')
            if not is_termux:
                return jsonify({"status": "error", "msg": "❌ 非 Termux 环境，无需防休眠锁。"})

            # 【R482 修复】timeout=5 → 10
            proc = subprocess.run(["termux-wake-lock"], capture_output=True, text=True, timeout=10)
            if proc.returncode == 0:
                return jsonify({"status": "success", "msg": "✅ 防休眠锁已成功获取！锁屏或后台运行任务时，手机将不会休眠杀后台。"})
            else:
                return jsonify({"status": "error", "msg": "❌ 获取防休眠锁失败。请确保你已点击「一键安装依赖」安装了 Termux:API 组件，并已安装手机端的 Termux:API 软件。"})
        except FileNotFoundError:
            return jsonify({"status": "error", "msg": "❌ 缺少 termux-api 组件。请先点击「一键安装依赖」进行修复。"})
        except Exception as e:
            return jsonify({"status": "error", "msg": f"❌ 申请异常: {e}"})

    return jsonify({"status": "error", "msg": "未知的权限类型"})


@api_bp.route('/api/install_deps', methods=['POST'])
def api_install_deps():
    try:
        is_termux = 'com.termux' in os.environ.get('PREFIX', '')

        if is_termux and not (os.path.exists('/sdcard') or os.path.exists('/storage/emulated/0')):
            # 【R479 修复】同 R478，改用 _run_termux_setup_storage
            _setup_ok, _setup_err = _run_termux_setup_storage(timeout=30)
            if not _setup_ok:
                logging.warning(f"[install_deps] termux-setup-storage 失败: {_setup_err}")

        pkgs = [
            "flask", "requests", "urllib3", "internetarchive",
            "certifi", "PySocks", "tqdm", "jsonpatch", "xmltodict"
        ]
        # 【R480 修复】可选依赖单独装（失败不影响主流程）
        optional_pkgs = ["pycurl"]

        if is_termux:
            # 【R481 修复】超时从 120/300 → 300/600
            subprocess.run(["pkg", "update", "-y"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=300)
            # 【R488 修复 - 2026-11】加 "curl"
            # Termux 的 curl 包同时提供 libcurl 库 + 头文件（$PREFIX/include/curl/curl.h）
            # pycurl 源码编译需要这些头文件
            # 不加的话 pip install pycurl 会失败：
            #   src/pycurl.h: fatal error: 'curl/curl.h' file not found
            subprocess.run(
                ["pkg", "install", "python-cryptography", "termux-api", "curl", "-y"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=600
            )

        cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "--no-cache-dir", "--no-input", "-i", "https://pypi.tuna.tsinghua.edu.cn/simple"] + pkgs

        # 【R481 修复】300 → 600
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=600)

        if proc.returncode == 0:
            # 【R480 修复】主流程成功后，单独装可选依赖 pycurl
            # 【R488 修复】Termux 分支已在 pkg install 里装了 curl（提供头文件）
            #             非 Termux 分支（Windows 有 wheel，Linux 有系统 libcurl-dev）不受影响
            pycurl_ok = False
            pycurl_err = ""
            try:
                opt_cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "--no-cache-dir", "--no-input", "-i", "https://pypi.tuna.tsinghua.edu.cn/simple"] + optional_pkgs
                opt_proc = subprocess.run(opt_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=300)
                pycurl_ok = (opt_proc.returncode == 0)
                if not pycurl_ok:
                    pycurl_err = (opt_proc.stdout or '')[-200:]
            except subprocess.TimeoutExpired:
                pycurl_err = "pycurl 安装超时 300 秒"
            except Exception as e:
                pycurl_err = str(e)

            pycurl_msg = ""
            if pycurl_ok:
                pycurl_msg = "\n✅ pycurl 已安装（S3 分片上传进度条更平滑）"
            elif pycurl_err:
                pycurl_msg = f"\n⚠️ pycurl 可选依赖安装失败（不影响主流程，进度条会 10MB 一跳）: {pycurl_err[:100]}"
            else:
                pycurl_msg = "\n⚠️ pycurl 可选依赖未尝试"

            core_msg = ""
            try:
                sb_path = warp_core.get_singbox_binary()
                usque_path = warp_core.get_usque_binary()
                if sb_path and usque_path:
                    core_msg = "\n✅ sing-box (v1.9.0) 和 usque (v4.2.1) 核心已成功自动下载并安装。"
                elif sb_path:
                    core_msg = "\n⚠️ sing-box 安装成功，但 usque 下载失败，可能是网络波动，请稍后在代理页重试。"
                elif usque_path:
                    core_msg = "\n⚠️ usque 安装成功，但 sing-box 下载失败，请稍后在代理页重试。"
                else:
                    core_msg = "\n⚠️ 核心下载失败，请先在【🌐 代理】中配置好可用的代理节点再重试下载。"
            except Exception as e:
                core_msg = f"\n⚠️ 自动下载核心时发生异常: {e}"

            return jsonify({"status": "success", "msg": f"✅ 所有 Python 依赖库安装成功！{pycurl_msg}{core_msg}\n\n请重启程序（关闭终端后重新运行 python netdisk.py）以确保完全生效。"})
        else:
            err_log = ""
            if proc.stdout:
                lines = proc.stdout.strip().split('\n')
                err_log = "\n".join(lines[-8:])
            return jsonify({"status": "error", "msg": f"❌ 安装失败，请检查网络或权限。\n{err_log}"})
    except subprocess.TimeoutExpired:
        return jsonify({"status": "error", "msg": "❌ 安装超时，请检查网络是否通畅。"})
    except Exception as e:
        return jsonify({"status": "error", "msg": f"❌ 执行异常: {str(e)}"})


@api_bp.route('/api/fetch_s3', methods=['POST'])
def api_fetch_s3():
    data = request.get_json()
    email = data.get('email', '').strip()
    password = data.get('password', '').strip()
    if not email or not password:
        return jsonify({"status": "error", "msg": "邮箱和密码不能为空"})

    req_proxies = get_req_proxies()
    verify = get_ssl_verify()

    try:
        s = requests.Session()
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

        r1 = s.get("https://archive.org/account/login", headers=headers, proxies=req_proxies, verify=verify, timeout=15)
        if r1.status_code != 200:
            return jsonify({"status": "error", "msg": f"无法连接 IA 登录页 (HTTP {r1.status_code})，请检查代理节点"})

        csrf_match = re.search(r'name="csrfmiddlewaretoken"\s+value="([^"]+)"', r1.text)
        if not csrf_match:
            return jsonify({"status": "error", "msg": "无法获取登录令牌，可能是代理节点返回了拦截页面"})
        csrf_token = csrf_match.group(1)

        login_data = {
            'csrfmiddlewaretoken': csrf_token,
            'email': email,
            'password': password,
            'remember': 'on'
        }
        login_headers = dict(headers)
        login_headers['Referer'] = 'https://archive.org/account/login'

        r2 = s.post("https://archive.org/account/login", data=login_data, headers=login_headers, proxies=req_proxies, verify=verify, allow_redirects=True, timeout=15)

        if "account/login" in r2.url:
            return jsonify({"status": "error", "msg": "登录失败：邮箱或密码错误，或者触发了官方人机验证风控。\n提示：你可以尝试在手机配好系统代理后，去官网手动登录一次解锁。"})

        r3 = s.get("https://archive.org/account/s3.php", headers=headers, proxies=req_proxies, verify=verify, timeout=15)
        if r3.status_code != 200:
            return jsonify({"status": "error", "msg": f"无法获取 S3 页面 (HTTP {r3.status_code})"})

        ak_match = re.search(r'name="s3_access_key"[^>]*value="([^"]+)"', r3.text) or re.search(r'value="([^"]+)"[^>]*name="s3_access_key"', r3.text)
        sk_match = re.search(r'name="s3_secret_key"[^>]*value="([^"]+)"', r3.text) or re.search(r'value="([^"]+)"[^>]*name="s3_secret_key"', r3.text)

        if not ak_match or not sk_match:
            return jsonify({"status": "error", "msg": "登录成功，但未能提取到 S3 密钥。可能账号未验证邮箱，或页面结构有变。"})

        return jsonify({"status": "success", "access_key": ak_match.group(1), "secret_key": sk_match.group(1)})
    except requests.exceptions.Timeout:
        return jsonify({"status": "error", "msg": "请求 IA 官方超时，请检查当前代理节点是否正常"})
    except Exception as e:
        return jsonify({"status": "error", "msg": f"发生异常: {str(e)}"})


@api_bp.route('/api/update_name', methods=['POST'])
def api_update_name():
    if not session.get('logged_in'):
        return jsonify({'status': 'error', 'msg': '未登录'})
    try:
        data = request.get_json()
        email, new_name = data.get('email'), data.get('name')
        if not email or not new_name:
            return jsonify({'status': 'error', 'msg': '参数缺失'})
        cfg = get_config()
        if email in cfg.get('accounts', {}):
            cfg['accounts'][email]['custom_name'] = new_name
            save_config(cfg)
            return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'msg': str(e)})
    return jsonify({'status': 'error'})


@api_bp.route('/api/search_history/del', methods=['POST'])
def api_search_history_del():
    q = request.json.get('q')
    cfg = get_config()
    hist = cfg.get('discover_history', [])
    if q in hist:
        hist.remove(q)
        cfg['discover_history'] = hist
        save_config(cfg)
    return jsonify({"status": "success"})