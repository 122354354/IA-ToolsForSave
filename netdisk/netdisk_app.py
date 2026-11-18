# netdisk_app.py
# 汇总文件：导出 app / require_login / handle_exception / _is_allowed_download_host
#          / DISCOVER_CACHE / PROXY_DL_IP_LOG / get_search_history / add_search_history
#          / _clean_virtual_tasks_worker
# 外部代码继续 from netdisk_app import xxx 即可，无需改动
#
# 【拆分完成 - 2026-11】
# 本文件原来是 netdisk_app.py 的完整实现（app 创建 + require_login + handle_exception
# + _clean_virtual_tasks_worker + favicon 路由 + 发现页缓存 + 搜索历史）。
# 现拆分为：
#   - netdisk_app_core.py    ：Flask app 创建 + secret_key + warp_bp + favicon 路由
#   - netdisk_app_guards.py  ：require_login + handle_exception + _is_allowed_download_host
#   - netdisk_app_discover.py：_clean_virtual_tasks_worker + DISCOVER_CACHE
#                              + PROXY_DL_IP_LOG + get_search_history + add_search_history
# 本文件为汇总文件，只负责 re-export，不含实际实现逻辑。
#
# 【副作用 import - 模块加载时必须执行】
# - import netdisk_app_core    ：触发 app 创建 + warp_bp 注册 + favicon 路由注册
# - import netdisk_app_guards  ：触发 @app.errorhandler + @app.before_request 注册
#                                （必须在 app 创建之后，所以放在 core 之后）
# - import netdisk_app_discover：触发 _maybe_clear_history_on_startup 在进程启动时执行
#                                （依赖 netdisk_core，所以放在最后）
#
# 【关键约束 - 后续维护必读】
# 1. 本文件不再自己定义 @app.route / @app.errorhandler / @app.before_request
#    （R468 / R472）—— 全部由子模块副作用注册
#    若在此处重复注册，会覆盖子模块的实现或引入分叉
# 2. DISCOVER_CACHE / PROXY_DL_IP_LOG 只从 netdisk_app_discover 导入
#    （R470 / R471）—— 确保同一对象，多个使用者（routes_views_discover / routes_api_browse）
#    共享
# 3. _is_allowed_download_host 只从 netdisk_app_guards 导入
#    （R467）—— 避免两份实现分叉
# 4. 不重复 app.secret_key / app.config['MAX_CONTENT_LENGTH'] / app.register_blueprint
#    （R472）—— 全部在 netdisk_app_core 里
# 5. netdisk_app_core / netdisk_app_guards / netdisk_app_discover 不被本文件之外的
#    文件直接引用（除非未来重构），所有外部访问都通过 netdisk_app 的 re-export
#
# 【对外接口（保持向后兼容）】
# - app                         （netdisk.py / 其他）
# - _clean_virtual_tasks_worker （netdisk.py 启动后台线程）
# - _is_allowed_download_host   （netdisk_routes_api_browse.py）
# - PROXY_DL_IP_LOG             （netdisk_routes_api_browse.py）
# - DISCOVER_CACHE              （netdisk_routes_views_discover.py）
# - get_search_history          （netdisk_routes_views_discover.py）
# - add_search_history          （netdisk_routes_views_discover.py）
# - require_login / handle_exception（re-export，外部一般不需要）
#
# 【📝 保留记录 - 2026-11，低优先，以后修】
#   R376 / R377 / R378（已修）：死代码混淆 → 本次拆分完成，不再是死代码
#   R466：netdisk_app_guards 与 netdisk_app 的 require_login / handle_exception
#        当前一致（R379 已补）→ 拆分完成后唯一实现在 guards，避免分叉
#   R467：_is_allowed_download_host 只从 guards 导入（本文件）
#   R468：不在本文件重复注册 @app.errorhandler / @app.before_request
#   R469：import netdisk_app_discover 触发 _maybe_clear_history_on_startup
#   R470：PROXY_DL_IP_LOG 从 discover 导入（与 netdisk_routes_api_browse 共享）
#   R471：DISCOVER_CACHE 从 discover 导入（与 netdisk_routes_views_discover 共享）
#   R472：不重复 app.secret_key / app.config / app.register_blueprint
#
# 【📝 同源问题记录 - 其他文件，后续统一修】
#   - 无

# 1. 先导入 core（触发 app 创建 + 注册 warp_bp + favicon 路由）
#    注意：必须在 guards / discover 之前（guards 依赖 app）
from netdisk_app_core import app

# 2. 再导入 guards（触发 @app.errorhandler / @app.before_request 注册）
#    注意：必须在 app 创建之后（依赖 app）
#    副作用 import + re-export（require_login / handle_exception 外部一般不需要，
#    但保留以防未来其他模块要用）
import netdisk_app_guards   # noqa: F401
from netdisk_app_guards import (
    require_login,
    handle_exception,
    _is_allowed_download_host,
)

# 3. 最后导入 discover（触发 _maybe_clear_history_on_startup 进程启动时执行一次）
#    注意：必须在 netdisk_core 之后（discover 依赖 netdisk_core）
#    副作用 import + re-export（外部通过这些名字访问）
import netdisk_app_discover   # noqa: F401
from netdisk_app_discover import (
    DISCOVER_CACHE,
    PROXY_DL_IP_LOG,
    get_search_history,
    add_search_history,
    _clean_virtual_tasks_worker,
)


__all__ = [
    # Flask app
    'app',
    # guards
    'require_login', 'handle_exception', '_is_allowed_download_host',
    # discover
    'DISCOVER_CACHE', 'PROXY_DL_IP_LOG',
    'get_search_history', 'add_search_history',
    '_clean_virtual_tasks_worker',
]
