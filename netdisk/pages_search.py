# pages_search.py
# 高级搜索页 HTML

SEARCH_HTML = r"""
<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">{{ style|safe }}</head><body>
{{ navbar|safe }}
<div class="card">
    <div style="margin-bottom:15px;"><button onclick="history.back()" class="btn btn-sm btn-gray">⬅️ 返回上一级</button></div>
    <h2>🔍 高级搜索</h2>
    <form method="GET" action="/search">
        <div style="display:flex; flex-direction:column; gap:12px;">
            <input type="text" name="q" value="{{ q }}" placeholder="输入搜索关键词..." style="margin:0;" required>
            <div style="display:flex; flex-wrap:wrap; gap:10px;">
                <select name="mode" style="flex:1; min-width:120px; margin:0;"><option value="include" {% if mode=='include' %}selected{% endif %}>包含 (LIKE)</option><option value="not_include" {% if mode=='not_include' %}selected{% endif %}>不包含 (NOT LIKE)</option><option value="startswith" {% if mode=='startswith' %}selected{% endif %}>开头是 (LIKE q%)</option><option value="endswith" {% if mode=='endswith' %}selected{% endif %}>结尾是 (LIKE %q)</option><option value="exact" {% if mode=='exact' %}selected{% endif %}>精确 (=)</option><option value="regex" {% if mode=='regex' %}selected{% endif %}>正则 (REGEXP)</option></select>
                <select name="scope" id="scope_select" style="flex:1; min-width:120px; margin:0;" onchange="document.getElementById('folder_select_box').style.display = this.value === 'folder' ? 'block' : 'none';"><option value="global" {% if scope=='global' %}selected{% endif %}>全局搜索</option><option value="folder" {% if scope=='folder' %}selected{% endif %}>指定文件夹</option></select>
            </div>
            <select name="folder_id" id="folder_select_box" style="margin:0; display: {% if scope=='folder' %}block{% else %}none{% endif %};"><option value="0">🏠 根目录</option>{% for f in all_folders %}<option value="{{ f[0] }}" {% if folder_id==f[0] %}selected{% endif %}>{{ f[1] }}</option>{% endfor %}</select>
            <button type="submit" class="btn btn-blue" style="margin:0;">🔍 开始搜索</button>
        </div>
    </form>
</div>
{% if q %}
<div class="card">
    <h3 style="margin-top:0;">搜索结果 (找到 {{ items|length }} 个条目)</h3>
    {% for i in items %}
    <div class="item-row"><div class="item-main"><div class="item-header"><div class="item-info"><strong id="title-{{ i[1] }}">{{ i[2] }}</strong><span class="file-stats" id="file-count-{{ i[1] }}" style="display: {% if i[5] > 0 %}inline-block{% else %}none{% endif %};">📂 {{ i[5] }} 个文件</span><span class="status-badge" style="background: {{ 'var(--green)' if i[3]=='done' else 'var(--danger)' }}">{{ i[3] }}</span><br><small>ID: {{ i[1] }}</small></div></div></div>
    <div class="item-actions"><a href="https://archive.org/details/{{ i[1] }}" target="_blank" class="btn btn-sm btn-blue">🌐 官方页</a><a href="/item_detail/{{ i[1] }}" class="btn btn-sm btn-gray">⚙️ 管理</a><a href="/upload?edit_id={{ i[0] }}" class="btn btn-sm btn-gray">✏️ 编辑</a><a href="/trash_item/{{ i[0] }}" class="btn btn-sm btn-red" onclick="return confirm('移入回收站？')">🗑️ 删除</a></div></div>
    {% endfor %}
    {% if not items %}<p style="color:#999; text-align:center; padding:40px 0;">没有找到匹配的条目。</p>{% endif %}
</div>
<script>
    (function() { 
        // 【核心修复 Bug 11】用 Jinja 的 tojson 安全注入，替代 "{{ q }}" 字符串拼接
        // tojson 会自动处理 "  \  换行  </script>  等危险字符
        let searchQ = {{ q|tojson }}; 
        if(!searchQ) return; 
        document.querySelectorAll('[id^="title-"]').forEach(el => { 
            let titleText = el.innerText; 
            try { 
                let reg = new RegExp(searchQ.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi'); 
                el.innerHTML = escapeHtml(titleText).replace(reg, match => `<mark>${match}</mark>`); 
            } catch(e) {} 
        }); 
    })();
</script>
{% endif %}
</body></html>
"""