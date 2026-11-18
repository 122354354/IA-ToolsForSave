# netdisk_routes_api.py
# 汇总文件：导出 api_bp，并通过副作用导入让各子模块在 api_bp 上注册路由
# 外部代码继续 from netdisk_routes_api import api_bp 即可，无需改动

from netdisk_blueprints import api_bp

# 副作用导入：触发各子模块注册路由到 api_bp
import netdisk_routes_api_misc        # noqa: F401
import netdisk_routes_api_browse      # noqa: F401
import netdisk_routes_api_download    # noqa: F401
import netdisk_routes_api_files       # noqa: F401
import netdisk_routes_api_upload      # noqa: F401
import netdisk_routes_api_tasks       # noqa: F401
import netdisk_routes_api_folders     # noqa: F401