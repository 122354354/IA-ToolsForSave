# netdisk_routes_views_discover.py
# 发现页：IA 全网搜索 + 搜索历史 + 分页 + DISCOVER_CACHE 缓存

from urllib.parse import quote, unquote
from flask import request, render_template_string, session

from netdisk_blueprints import views_bp
from netdisk_core import cfg_get, ia_get
from templates_module import STYLE, get_navbar
from pages_module import DISCOVER_HTML
from netdisk_app import get_search_history, add_search_history, DISCOVER_CACHE


@views_bp.route('/discover')
def discover_view():
    raw_q = request.args.get('q', '')
    decoded_q = unquote(raw_q).strip()
    page = request.args.get('page', 1, type=int)
    if 'q' in request.args:
        session['discover_q'] = decoded_q
        session['discover_page'] = page
        if page == 1 and decoded_q:
            add_search_history(decoded_q)
    q = session.get('discover_q', '')
    current_page = session.get('discover_page', 1)
    items, total = [], 0
    proxy_dl = cfg_get('download_via_proxy', True)
    history = get_search_history()
    if q:
        cache_key = f"{q}_{current_page}"
        if cache_key in DISCOVER_CACHE:
            items, total = DISCOVER_CACHE[cache_key]
        else:
            rows = 20
            encoded_q = quote(q)
            url = f"https://archive.org/advancedsearch.php?q={encoded_q}&fl[]=identifier&fl[]=title&fl[]=creator&fl[]=description&fl[]=downloads&fl[]=stars&fl[]=date&fl[]=mediatype&sort[]=downloads+desc&rows={rows}&page={current_page}&output=json"
            resp = ia_get(url, timeout=15)
            if resp and resp.status_code == 200:
                try:
                    data = resp.json().get('response', {})
                    items = data.get('docs', [])
                    total = data.get('numFound', 0)
                    DISCOVER_CACHE[cache_key] = (items, total)
                except:
                    pass
    return render_template_string(
        DISCOVER_HTML, s=STYLE,
        n=get_navbar(False, active_page='discover'),
        q=quote(q), raw_q=q, items=items, total=total,
        proxy_dl=proxy_dl, page=current_page, history=history
    )