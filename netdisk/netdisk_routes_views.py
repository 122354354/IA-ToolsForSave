# netdisk_routes_views.py
# 汇总文件：导出 views_bp，并通过副作用导入让各子模块在 views_bp 上注册路由
# 外部代码继续 from netdisk_routes_views import views_bp 即可，无需改动

from netdisk_blueprints import views_bp

# 副作用导入：触发各子模块注册路由到 views_bp
import netdisk_routes_views_auth         # noqa: F401
import netdisk_routes_views_index        # noqa: F401
import netdisk_routes_views_upload       # noqa: F401
import netdisk_routes_views_manage       # noqa: F401
import netdisk_routes_views_folder       # noqa: F401
import netdisk_routes_views_settings     # noqa: F401
import netdisk_routes_views_discover     # noqa: F401  ← 本次补齐