# netdisk_state_paths.py
# 路径常量 + 目录创建

import os

HOME_DIR = os.path.expanduser("~")
DB_FILE = os.path.join(HOME_DIR, "netdisk.db")
CONFIG_FILE = os.path.join(HOME_DIR, "config.json")
TEMP_UPLOAD_DIR = os.path.join(HOME_DIR, "temp_upload")
THUMB_DIR = os.path.join(HOME_DIR, "thumb_cache")
WARP_DIR = os.path.join(HOME_DIR, "warp_engine")
BACKUP_DIR = os.path.join(HOME_DIR, "backups")

os.makedirs(TEMP_UPLOAD_DIR, exist_ok=True)
os.makedirs(THUMB_DIR, exist_ok=True)
os.makedirs(WARP_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)