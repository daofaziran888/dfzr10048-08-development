"""业务路由汇总：分段交付专用路由先于通用资源路由注册，其余模块共用一套资源路由，由领域服务完成分派。"""
from __future__ import annotations

from app.routers import resource, segment_delivery

ROUTERS = [segment_delivery, resource]
