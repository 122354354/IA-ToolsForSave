# warp_module.py
# 此文件作为统一入口，汇总拆分出去的各个 warp 模块，保持与 netdisk.py 的兼容

# 暴露给 netdisk.py 主程序调用的函数
from warp_core import autostart_saved_proxies

# 暴露给 netdisk.py 注册的 Flask 蓝图
from warp_routes import warp_bp