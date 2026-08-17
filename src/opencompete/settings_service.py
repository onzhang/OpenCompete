"""本地单用户设置服务：公开运行参数与 `.env` 凭据隔离。."""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

import tomlkit
from pydantic import ValidationError

from opencompete.errors import ConfigurationError, DataValidationError, PersistenceError
from opencompete.models import RunStatus
from opencompete.security import validate_public_http_url
from opencompete.settings import (
    MANAGEABLE_MODEL_PROVIDERS,
    MANAGEABLE_SETTINGS_FIELDS,
    MODEL_SETTINGS_FIELDS,
    SETTINGS_GROUPS,
    OpenCompeteSettings,
    load_settings,
)

SETTINGS_SCHEMA_VERSION = 1
CredentialSource = Literal["未配置", "本地 .env", "外部环境"]

_CREDENTIAL_ENV_NAMES = frozenset(
    {
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
        "GOOGLE_API_KEY",
        "TAVILY_API_KEY",
    }
)
_BASE_URL_ENV_NAMES = frozenset({"OPENAI_BASE_URL", "ANTHROPIC_BASE_URL"})
_SECRET_ENV_MARKERS = ("API_KEY", "BASE_URL")
_TERMINAL_RUN_STATUSES = frozenset(
    {
        RunStatus.COMPLETED,
        RunStatus.PARTIAL,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    }
)


@dataclass(frozen=True)
class CredentialStatus:
    """凭据的公开状态，绝不含值或可用于识别值的片段。."""

    name: str
    configured: bool
    source: CredentialSource
    manageable: bool

    def view(self) -> dict[str, object]:
        """生成 API 可返回的零回显视图。."""
        return {
            "name": self.name,
            "configured": self.configured,
            "source": self.source,
            "manageable": self.manageable,
        }


@dataclass(frozen=True)
class SettingsSnapshot:
    """在创建新任务时冻结的非秘密设置与修订版本。."""

    settings: OpenCompeteSettings
    revision: int


class SettingsService:
    """协调 TOML 公开设置、`.env` 凭据和当前进程默认设置。."""

    def __init__(
        self,
        config_path: str | Path,
        *,
        startup_environment: Mapping[str, str] | None = None,
        initial_settings: OpenCompeteSettings | None = None,
    ) -> None:
        """绑定配置路径并捕获启动前存在的外部环境覆盖。."""
        self.config_path = Path(config_path).resolve()
        self.dotenv_path = self.config_path.parent / ".env"
        self._startup_environment = dict(startup_environment or os.environ)
        self._lock = asyncio.Lock()
        self._settings = initial_settings or load_settings(self.config_path)
        self._revision = self._calculate_revision()

    @property
    def lock(self) -> asyncio.Lock:
        """供任务创建与配置写入共享的单进程互斥锁。."""
        return self._lock

    @property
    def settings(self) -> OpenCompeteSettings:
        """当前仅供新任务采用的非秘密默认设置。."""
        return self._settings

    @property
    def revision(self) -> int:
        """当前公开设置修订号。."""
        return self._revision

    async def snapshot_for_new_run(self) -> SettingsSnapshot:
        """在与写入共享的锁中取得不可变设置快照。."""
        async with self._lock:
            return SettingsSnapshot(self._settings, self._revision)

    async def public_view(self) -> dict[str, object]:
        """返回严格白名单的设置、来源、修订号和凭据状态。."""
        async with self._lock:
            return self._public_view_unlocked()

    async def update_settings(
        self,
        updates: Mapping[str, Any],
        *,
        expected_revision: int,
        on_updated: Callable[[OpenCompeteSettings, int], None] | None = None,
    ) -> dict[str, object]:
        """原子更新 TOML 中可管理的非秘密字段。."""
        async with self._lock:
            self._check_revision(expected_revision)
            unknown = set(updates) - MANAGEABLE_SETTINGS_FIELDS
            if unknown:
                fields = ", ".join(sorted(unknown))
                raise DataValidationError(f"设置包含不允许由页面修改的字段：{fields}")
            locked = self._externally_managed_fields(set(updates))
            if locked:
                fields = ", ".join(sorted(locked))
                raise DataValidationError(f"以下设置由外部环境管理，不能在页面保存：{fields}")
            self._validate_manageable_model_providers(updates)
            try:
                candidate = self._settings.model_copy(update=dict(updates))
                candidate = type(self._settings).model_validate(
                    candidate.model_dump(mode="python")
                )
            except ValidationError as exc:
                raise ConfigurationError(f"设置校验失败：{exc}") from exc
            self._write_toml_updates(dict(updates))
            self._settings = candidate
            self._revision += 1
            if on_updated is not None:
                on_updated(candidate, self._revision)
            return self._public_view_unlocked()

    async def update_credential(
        self,
        name: str,
        *,
        operation: Literal["set", "clear"],
        value: str | None,
        expected_revision: int,
        active_run_statuses: Mapping[str, RunStatus] | None = None,
        active_statuses_loader: (
            Callable[[], Awaitable[Mapping[str, RunStatus]]]
        )
        | None = None,
    ) -> dict[str, object]:
        """设置或清除一个白名单 `.env` 凭据，不回显提交值。."""
        async with self._lock:
            self._check_revision(expected_revision)
            env_name = name.strip().upper()
            if env_name not in _CREDENTIAL_ENV_NAMES:
                raise DataValidationError("不支持管理该凭据")
            if self._is_externally_managed(env_name):
                raise DataValidationError("该凭据由进程外环境管理，不能在页面修改")
            statuses = (
                await active_statuses_loader()
                if active_statuses_loader is not None
                else active_run_statuses or {}
            )
            blocked = sorted(
                run_id
                for run_id, status in statuses.items()
                if status not in _TERMINAL_RUN_STATUSES
            )
            if blocked:
                raise DataValidationError(
                    "存在未终态任务，完成或停止后才能替换凭据"
                )
            if operation == "set":
                if value is None or not value.strip():
                    raise DataValidationError("设置凭据时必须提供非空值")
                normalized = self._validate_credential_value(env_name, value)
                self._write_dotenv_value(env_name, normalized)
                os.environ[env_name] = normalized
            elif operation == "clear":
                if value is not None:
                    raise DataValidationError("清除凭据时不能提交凭据值")
                self._remove_dotenv_value(env_name)
                os.environ.pop(env_name, None)
            else:
                raise DataValidationError("凭据操作必须是 set 或 clear")
            self._revision += 1
            return self._public_view_unlocked()

    def _public_view_unlocked(self) -> dict[str, object]:
        values = self._settings.model_dump(mode="json")
        fields = {
            name: {
                "value": values[name],
                "source": self._field_source(name),
                "group": next(
                    group for group, members in SETTINGS_GROUPS.items() if name in members
                ),
            }
            for name in sorted(MANAGEABLE_SETTINGS_FIELDS)
        }
        credentials = [
            self._credential_status(name).view()
            for name in sorted(_CREDENTIAL_ENV_NAMES)
        ]
        return {
            "schema_version": SETTINGS_SCHEMA_VERSION,
            "revision": self._revision,
            "effective_for": "仅新建任务；已创建任务继续使用创建时的非秘密设置快照",
            "fields": fields,
            "groups": SETTINGS_GROUPS,
            "credentials": credentials,
        }

    def _validate_manageable_model_providers(
        self, updates: Mapping[str, Any]
    ) -> None:
        unsupported: list[str] = []
        for name in MODEL_SETTINGS_FIELDS:
            value = updates.get(name)
            if value is None:
                continue
            if not isinstance(value, str):
                continue
            provider, _, _model = value.partition(":")
            if provider not in MANAGEABLE_MODEL_PROVIDERS:
                unsupported.append(name)
        if unsupported:
            fields = ", ".join(sorted(unsupported))
            raise DataValidationError(
                f"以下模型路由需由外部环境管理，页面暂不支持：{fields}"
            )

    def _field_source(self, name: str) -> str:
        if self._is_externally_managed(f"OPENCOMPETE_{name.upper()}"):
            return "外部环境"
        document = self._read_toml()
        return "TOML" if name in document else "默认值"

    def _credential_status(self, name: str) -> CredentialStatus:
        value = os.environ.get(name, "").strip()
        if not value:
            return CredentialStatus(name, False, "未配置", True)
        external = self._is_externally_managed(name)
        return CredentialStatus(
            name,
            True,
            "外部环境" if external else "本地 .env",
            not external,
        )

    def _check_revision(self, expected_revision: int) -> None:
        if expected_revision != self._revision:
            raise DataValidationError("设置版本已变化，请刷新后重新保存")

    def _externally_managed_fields(self, fields: set[str]) -> set[str]:
        return {
            field
            for field in fields
            if self._is_externally_managed(f"OPENCOMPETE_{field.upper()}")
        }

    def _is_externally_managed(self, name: str) -> bool:
        value = self._startup_environment.get(name)
        return bool(value and value.strip())

    def _read_toml(self) -> tomlkit.TOMLDocument:
        if not self.config_path.exists():
            return tomlkit.document()
        try:
            return tomlkit.parse(self.config_path.read_text(encoding="utf-8"))
        except (OSError, tomlkit.exceptions.ParseError) as exc:
            raise ConfigurationError("无法读取有效的本地设置文件") from exc

    def _write_toml_updates(self, updates: Mapping[str, Any]) -> None:
        document = self._read_toml()
        for name, value in updates.items():
            if value is None:
                document.pop(name, None)
            else:
                document[name] = value
        self._atomic_write(self.config_path, tomlkit.dumps(document))

    def _read_dotenv_lines(self) -> list[str]:
        if not self.dotenv_path.exists():
            return []
        try:
            return self.dotenv_path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise PersistenceError("无法读取本地 .env 文件") from exc

    def _write_dotenv_value(self, name: str, value: str) -> None:
        lines = self._read_dotenv_lines()
        index = self._controlled_dotenv_line_index(lines, name)
        serialized = f"{name}={self._dotenv_quote(value)}"
        if index is None:
            lines.append(serialized)
        else:
            lines[index] = serialized
        self._atomic_write(self.dotenv_path, "\n".join(lines) + "\n")

    def _remove_dotenv_value(self, name: str) -> None:
        lines = self._read_dotenv_lines()
        index = self._controlled_dotenv_line_index(lines, name)
        if index is None:
            return
        del lines[index]
        payload = "\n".join(lines)
        if payload:
            payload += "\n"
        self._atomic_write(self.dotenv_path, payload)

    def _controlled_dotenv_line_index(
        self, lines: list[str], target: str
    ) -> int | None:
        seen: dict[str, int] = {}
        for index, raw_line in enumerate(lines):
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("export "):
                raise DataValidationError(".env 包含 export 语法，请先手工整理后再管理凭据")
            if "=" not in raw_line:
                raise DataValidationError(".env 包含复杂定义，请先手工整理后再管理凭据")
            raw_name, _raw_value = raw_line.split("=", 1)
            name = raw_name.strip()
            if not name or name != raw_name or name not in _CREDENTIAL_ENV_NAMES:
                continue
            if name in seen:
                raise DataValidationError(".env 中存在重复的受管凭据定义，请先手工整理")
            seen[name] = index
        return seen.get(target)

    def _validate_credential_value(self, name: str, value: str) -> str:
        normalized = value.strip()
        if "\x00" in normalized or "\n" in normalized or "\r" in normalized:
            raise DataValidationError("凭据不能包含换行或空字符")
        if name in _BASE_URL_ENV_NAMES:
            try:
                parsed = urlsplit(normalized)
                if parsed.query or parsed.fragment:
                    raise ValueError("Base URL 不能包含查询参数或片段")
                public = validate_public_http_url(normalized)
                public_parts = urlsplit(public)
            except ValueError as exc:
                raise DataValidationError(f"Base URL 无效：{exc}") from exc
            if public_parts.scheme != "https":
                raise DataValidationError("Base URL 必须使用 HTTPS")
            return urlunsplit(
                (
                    public_parts.scheme,
                    public_parts.netloc,
                    public_parts.path.rstrip("/"),
                    "",
                    "",
                )
            )
        return normalized

    def _dotenv_quote(self, value: str) -> str:
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

    def _atomic_write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                newline="\n",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_path = Path(temporary.name)
            os.replace(temporary_path, path)
        except OSError as exc:
            try:
                temporary_path.unlink(missing_ok=True)
            except UnboundLocalError:
                pass
            raise PersistenceError("无法原子保存本地设置") from exc

    def _dotenv_public_generation(self) -> int:
        """只按受管凭据槽位的存在状态生成非秘密 revision 输入。."""
        return sum(
            1 << index
            for index, name in enumerate(sorted(_CREDENTIAL_ENV_NAMES))
            if os.environ.get(name, "").strip()
        )

    def _calculate_revision(self) -> int:
        digest = hashlib.sha256()
        digest.update(self.config_path.name.encode())
        if self.config_path.exists():
            digest.update(self.config_path.read_bytes())
        # 凭据写入也必须推进 revision，但其真实内容绝不能参与 revision 推导，
        # 否则版本值可成为秘密的稳定派生指纹。
        digest.update(str(self._dotenv_public_generation()).encode())
        # JSON 数字需可被浏览器精确表示，保留 48 位摘要足以作为乐观锁版本。
        return int.from_bytes(digest.digest()[:6], "big")
