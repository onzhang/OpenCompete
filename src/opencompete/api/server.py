"""应用装配与启动入口。.

``create_app`` 在 lifespan 里构造全部单例（Runner / Repository /
EventHub / 任务注册表 / meta 索引 / 确认缓存）；``main`` 提供
``opencompete-api`` 命令行入口，固定单进程单 worker——
per-run 锁按事件循环分桶，多 worker 会破坏并发保护。
"""

from __future__ import annotations

import argparse
import logging
import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from opencompete.api.errors import register_exception_handlers
from opencompete.api.events import EventHub
from opencompete.api.meta import RunMetaStore
from opencompete.api.routes import router
from opencompete.api.tasks import ResearchTaskRegistry
from opencompete.settings import OpenCompeteSettings, load_settings
from opencompete.settings_service import SettingsService

logger = logging.getLogger(__name__)


def create_app(
    settings: OpenCompeteSettings,
    *,
    config_path: str | Path | None = None,
    startup_environment: Mapping[str, str] | None = None,
    settings_service: SettingsService | None = None,
) -> FastAPI:
    """按给定配置装配 FastAPI 应用。."""
    resolved_path = Path(config_path or "opencompete.toml").resolve()
    environment = dict(startup_environment or os.environ)
    service = settings_service or SettingsService(
        resolved_path,
        startup_environment=environment,
        initial_settings=settings,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """启动时构造单例，关闭时给活动研究一个宽限期。."""
        # 重依赖（langgraph 等）延迟到进程启动时导入。
        from opencompete.application import ApplicationRunner
        from opencompete.persistence.repository import RunRepository

        hub = EventHub()
        repository = RunRepository(settings.data_dir)
        app.state.settings = service.settings
        app.state.settings_service = service
        app.state.hub = hub
        app.state.repository = repository
        app.state.runner = ApplicationRunner(
            service.settings, repository=repository, progress_sink=hub.sink
        )
        app.state.registry = ResearchTaskRegistry(capacity=1)
        app.state.meta_store = RunMetaStore(Path(settings.data_dir))
        app.state.confirmation_cache = {}
        logger.info(
            "OpenCompete API 已就绪，数据目录: %s", settings.data_dir
        )
        yield
        await app.state.registry.shutdown(grace_seconds=5.0)

    app = FastAPI(
        title="OpenCompete API",
        description="本地竞品研究控制台的后端接口",
        lifespan=lifespan,
    )
    register_exception_handlers(app)
    app.include_router(router)
    return app


def main() -> None:
    """``opencompete-api`` 命令行入口。."""
    import uvicorn

    parser = argparse.ArgumentParser(
        prog="opencompete-api",
        description="启动 OpenCompete 本地 API 服务（单进程单 worker）",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="opencompete.toml 路径（默认取当前目录）",
    )
    parser.add_argument("--host", default="127.0.0.1", help="监听地址")
    parser.add_argument("--port", type=int, default=8123, help="监听端口")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config_path = Path(args.config or "opencompete.toml").resolve()
    startup_environment = dict(os.environ)
    settings = load_settings(config_path=config_path)
    app = create_app(
        settings,
        config_path=config_path,
        startup_environment=startup_environment,
    )
    # 传入应用对象即固定单 worker；研究进行中请勿停止服务，
    # 否则会留下无法自动恢复的中间态任务。
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
