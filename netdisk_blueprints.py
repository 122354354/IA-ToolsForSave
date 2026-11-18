# netdisk_blueprints.py
# 集中定义所有 Flask Blueprint，避免各路由子模块与汇总文件循环导入

from flask import Blueprint

api_bp = Blueprint('api', __name__)
views_bp = Blueprint('views', __name__)