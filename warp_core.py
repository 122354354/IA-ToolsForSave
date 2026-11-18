# warp_core.py
# 汇总文件：统一导出路径、平台、配置、二进制、生成器、进程管理
# 外部代码继续 from warp_core import xxx 即可，无需改动
# 【注意】WIREPROXY_PROCESSES 从 warp_config 导出的是 dict 引用，
#         外部代码若 mutate（[k]=/del）会同步；但不要重绑定

from warp_config import (
    HOME_DIR, CONFIG_FILE, WARP_DIR, WIREPROXY_PROCESSES, IS_WINDOWS,
    get_config, save_config, is_port_open, is_port_available,
)
from warp_binaries import (
    get_os_arch_str, get_singbox_binary, get_usque_binary, install_core_file,
)
from warp_generate import (
    get_available_proxy, start_custom_warp,
)
from warp_process import (
    stop_warp, autostart_saved_proxies,
)

__all__ = [
    # 常量与配置
    'HOME_DIR', 'CONFIG_FILE', 'WARP_DIR', 'WIREPROXY_PROCESSES', 'IS_WINDOWS',
    'get_config', 'save_config', 'is_port_open', 'is_port_available',
    # 二进制
    'get_os_arch_str', 'get_singbox_binary', 'get_usque_binary', 'install_core_file',
    # 生成器
    'get_available_proxy', 'start_custom_warp',
    # 进程管理
    'stop_warp', 'autostart_saved_proxies',
]