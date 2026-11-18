# pages_index_history.py
# 历史上传记录页 HTML

HISTORY_HTML = r"""
<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">{{ style|safe }}</head><body>
{{ navbar|safe }}
<div class="card">
    <div style="margin-bottom:15px;"><button onclick="history.back()" class="btn btn-sm btn-gray">⬅️ 返回上一级</button></div>
    <h2>📜 历史上传记录</h2>
    {% for i in items %}
    <div class="item-row">
        <div class="item-main"><div class="item-header"><div class="item-info"><strong>{{ i[2] }}</strong>{% if i[5] > 0 %}<span class="file-stats">📂 {{ i[5] }} 个文件</span>{% endif %}<br><small>ID: {{ i[1] }}</small></div><span class="status-badge" style="background:{{ 'var(--green)' if i[3]=='done' else ('var(--orange)' if i[3]=='cloud_missing' else 'var(--danger)') }}">{{ i[3] }}</span></div>{% if i[3] == 'error' or i[3] == 'cloud_missing' %}<div style="background:#ffebee; padding:10px; border-radius:6px; color:var(--danger); font-size:13px; word-break:break-all;">{{ i[4] or '云端已丢失，本地保留记录' }}</div>{% endif %}</div>
        <div class="item-actions"><a href="https://archive.org/details/{{ i[1] }}" target="_blank" class="btn btn-sm btn-blue">🌐 官方页</a><button class="btn btn-sm btn-blue" data-title="{{ i[2] }}" data-id="{{ i[1] }}" onclick="copyMarkdownFrom(this)" style="background:var(--blue);">🔗 MD</button><a href="/item_detail/{{ i[1] }}" class="btn btn-sm btn-gray">⚙️ 管理文件</a><a href="/upload?edit_id={{ i[0] }}" class="btn btn-sm btn-gray">✏️ 编辑元数据</a><a href="/trash_item/{{ i[0] }}" class="btn btn-sm btn-red" onclick="return confirm('移入回收站？')">🗑️ 删除</a></div>
    </div>
    {% endfor %}
    {% if not items %}<p style="color:#999; text-align:center; padding:40px 0;">目前没有任何上传记录。</p>{% endif %}
</div>
<script>function copyMarkdownFrom(btn) { let md = `> **${btn.dataset.title}**\n> [点击前往 Internet Archive 详情页](https://archive.org/details/${btn.dataset.id})`; if(navigator.clipboard) navigator.clipboard.writeText(md).then(()=>alert('✅ 已复制 Markdown')); else alert(md); }</script>
</body></html>
"""