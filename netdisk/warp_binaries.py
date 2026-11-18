# warp_binaries.py
# 平台架构识别、sing-box / usque 核心下载与安装

import os
import platform
import zipfile
import tarfile
import shutil
import requests
import warp_config
from warp_config import get_config, WARP_DIR, IS_WINDOWS


def get_os_arch_str():
    arch = platform.machine().lower()
    if arch in ('aarch64', 'arm64'): arch_str = "arm64"
    elif arch.startswith('arm'): arch_str = "arm"
    elif arch in ('x86_64', 'amd64'): arch_str = "amd64"
    elif arch in ('i386', 'i686'): arch_str = "386"
    else: arch_str = "amd64"

    sys_os = platform.system().lower()
    if sys_os == 'windows': os_str = 'windows'
    elif sys_os == 'darwin': os_str = 'darwin'
    else: os_str = 'linux'

    return f"{os_str}-{arch_str}"


def get_singbox_binary():
    bin_name = "sing-box.exe" if IS_WINDOWS else "sing-box"
    bin_path = os.path.join(WARP_DIR, bin_name)

    if os.path.exists(bin_path):
        if not IS_WINDOWS and not os.access(bin_path, os.X_OK):
            try: os.chmod(bin_path, 0o755)
            except: pass
        return bin_path

    cfg = get_config()
    proxy_url = cfg.get('proxy', '')
    req_proxies = None
    if proxy_url:
        proxy_h = proxy_url.replace("socks5://", "socks5h://") if proxy_url.startswith("socks5://") else proxy_url
        req_proxies = {"http": proxy_h, "https": proxy_h}

    os_arch = get_os_arch_str()
    ext = "zip" if IS_WINDOWS else "tar.gz"
    official_url = f"https://github.com/SagerNet/sing-box/releases/download/v1.9.0/sing-box-1.9.0-{os_arch}.{ext}"
    mirrors = ["https://gh-proxy.org/", "https://v4.gh-proxy.org/", "https://gh-proxy.com/"]
    urls_to_try = [f"{m}{official_url}" for m in mirrors] + [official_url]

    for url in urls_to_try:
        try:
            r = requests.get(url, stream=True, timeout=30, proxies=req_proxies, verify=False)
            if r.status_code == 200:
                if IS_WINDOWS:
                    zip_path = os.path.join(WARP_DIR, "sb.zip")
                    with open(zip_path, 'wb') as f:
                        for chunk in r.iter_content(8192): f.write(chunk)
                    with zipfile.ZipFile(zip_path, 'r') as z:
                        for name in z.namelist():
                            if name.endswith("sing-box.exe"):
                                with z.open(name) as source, open(bin_path, "wb") as target:
                                    shutil.copyfileobj(source, target)
                                break
                    os.remove(zip_path)
                else:
                    tar_path = os.path.join(WARP_DIR, "sb.tar.gz")
                    with open(tar_path, 'wb') as f:
                        for chunk in r.iter_content(8192): f.write(chunk)
                    with tarfile.open(tar_path, "r:gz") as tar:
                        for member in tar.getmembers():
                            if member.name.endswith("sing-box"):
                                member.name = "sing-box"
                                tar.extract(member, WARP_DIR)
                                break
                    os.remove(tar_path)

                if os.path.exists(bin_path):
                    if not IS_WINDOWS: os.chmod(bin_path, 0o755)
                    return bin_path
        except: continue
    return None


def get_usque_binary():
    bin_name = "usque.exe" if IS_WINDOWS else "usque"
    bin_path = os.path.join(WARP_DIR, bin_name)

    if os.path.exists(bin_path):
        if not IS_WINDOWS and not os.access(bin_path, os.X_OK):
            try: os.chmod(bin_path, 0o755)
            except: pass
        return bin_path

    cfg = get_config()
    proxy_url = cfg.get('proxy', '')
    req_proxies = None
    if proxy_url:
        proxy_h = proxy_url.replace("socks5://", "socks5h://") if proxy_url.startswith("socks5://") else proxy_url
        req_proxies = {"http": proxy_h, "https": proxy_h}

    os_arch = get_os_arch_str()
    usque_arch = os_arch.replace('-', '_')
    official_url = f"https://github.com/Diniboy1123/usque/releases/download/v4.2.1/usque_4.2.1_{usque_arch}.zip"
    mirrors = ["https://gh-proxy.org/", "https://v4.gh-proxy.org/", "https://gh-proxy.com/"]
    urls_to_try = [f"{m}{official_url}" for m in mirrors] + [official_url]

    for url in urls_to_try:
        try:
            r = requests.get(url, stream=True, timeout=30, proxies=req_proxies, verify=False)
            if r.status_code == 200:
                zip_path = os.path.join(WARP_DIR, "usque.zip")
                with open(zip_path, 'wb') as f:
                    for chunk in r.iter_content(8192): f.write(chunk)

                with zipfile.ZipFile(zip_path, 'r') as z:
                    for name in z.namelist():
                        if name.endswith("usque.exe") or name.endswith("usque"):
                            with z.open(name) as source, open(bin_path, "wb") as target:
                                shutil.copyfileobj(source, target)
                            break
                os.remove(zip_path)
                if os.path.exists(bin_path):
                    if not IS_WINDOWS: os.chmod(bin_path, 0o755)
                    return bin_path
        except: continue
    return None


def install_core_file(tmp_path, fname, target_name):
    target_bin = os.path.join(WARP_DIR, target_name)
    success = False
    try:
        if fname.endswith('.tar.gz') or fname.endswith('.tgz'):
            with tarfile.open(tmp_path, "r:gz") as tar:
                for member in tar.getmembers():
                    base_name = os.path.basename(member.name)
                    if base_name in ("sing-box", "usque"):
                        member.name = target_name
                        tar.extract(member, WARP_DIR)
                        success = True
                        break
        elif fname.endswith('.zip'):
            with zipfile.ZipFile(tmp_path, "r") as z:
                for name in z.namelist():
                    base_name = os.path.basename(name)
                    if base_name in ("sing-box", "usque", "sing-box.exe", "usque.exe"):
                        with z.open(name) as source, open(target_bin, "wb") as target:
                            shutil.copyfileobj(source, target)
                        success = True
                        break
        else:
            shutil.copy(tmp_path, target_bin)
            success = True

        if success and os.path.exists(target_bin):
            if not IS_WINDOWS: os.chmod(target_bin, 0o755)
            return True, ""
    except Exception as e:
        return False, str(e)
    return False, "压缩包内未找到有效核心文件"