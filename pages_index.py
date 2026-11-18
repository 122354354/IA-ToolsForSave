# pages_index.py
# 汇总文件：统一导出首页 / 历史 / 回收站 HTML
# 外部代码继续 from pages_index import INDEX_HTML, HISTORY_HTML, TRASH_HTML 即可

from pages_index_main import INDEX_HTML
from pages_index_history import HISTORY_HTML
from pages_index_trash import TRASH_HTML

__all__ = ['INDEX_HTML', 'HISTORY_HTML', 'TRASH_HTML']