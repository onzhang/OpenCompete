"""本地 FastAPI 服务层：包装 ApplicationRunner，为前端提供 HTTP 接口。.

本包只做协议转换与进程内编排，不改变任何业务语义：
- 领域校验、预算、确认点与持久化全部由 ``opencompete.application`` 负责；
- 响应数据显式挑选字段，``config_snapshot`` 与未脱敏异常绝不透传；
- 事件直播以 ``events.jsonl`` 为唯一权威源，``progress_sink`` 仅作唤醒信号。
"""
