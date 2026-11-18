# pages_queue.py
# 汇总文件：用普通字符串拼接（不用 f-string，避免误解析 Jinja 的 {{ }} / {% %}）
# 外部代码继续 from pages_queue import QUEUE_HTML 即可

from pages_queue_style import QUEUE_STYLE
from pages_queue_body import QUEUE_BODY
from pages_queue_js import QUEUE_JS

QUEUE_HEAD = '<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">{{ style|safe }}\n'

QUEUE_HTML = QUEUE_HEAD + QUEUE_STYLE + QUEUE_BODY + QUEUE_JS

__all__ = ['QUEUE_HTML']