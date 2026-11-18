# pages_index_trash.py
# 回收站页 HTML

TRASH_HTML = r"""
<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">{{ style|safe }}</head><body>
{{ navbar|safe }}
<div class="card">
    <h2>🗑️ 回收站</h2>
    <form method="POST" action="/empty_trash" onsubmit="return confirm('确定清空回收站吗？该操作会永久删除对应临时文件！');" style="margin-bottom:15px;"><button type="submit" class="btn btn-red">🔥 清空回收站</button></form>
    {% for i in items %}
    <div class="item-row"><div class="item-main"><strong style="word-break: break-all;">{{ i[2] }}</strong><br><small>ID: {{ i[1] }}</small></div><div class="item-actions"><a href="/restore_item/{{ i[0] }}" class="btn btn-sm btn-green">♻️ 恢复</a><a href="/purge_item/{{ i[0] }}" class="btn btn-sm btn-red" onclick="return confirm('彻底删除本地记录？')">🔥 永久删除</a></div></div>
    {% endfor %}
    {% if not items %}<p style="color:#999; text-align:center; padding:40px 0;">回收站是空的。</p>{% endif %}
</div></body></html>
"""