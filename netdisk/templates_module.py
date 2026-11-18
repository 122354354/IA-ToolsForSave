# templates_module.py
# 汇总文件：统一导出全局样式与导航栏构建函数
# 外部代码继续 from templates_module import STYLE, get_navbar 即可，无需改动

from templates_style import STYLE
from templates_navbar import get_navbar

__all__ = ['STYLE', 'get_navbar']