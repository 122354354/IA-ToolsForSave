# pages_settings.py
# 汇总文件：用普通字符串拼接（不用 f-string，避免误解析 Jinja 的 {{ }} / {% %}）
# 外部代码继续 from pages_settings import SETTINGS_HTML 即可

from pages_settings_body import SETTINGS_BODY
from pages_settings_js import SETTINGS_JS

SETTINGS_HTML = SETTINGS_BODY + SETTINGS_JS

__all__ = ['SETTINGS_HTML']