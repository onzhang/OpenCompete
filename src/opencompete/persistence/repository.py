"""长期业务产物的本地目录仓库."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import threading
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from opencompete.errors import PersistenceError
from opencompete.models import (
    RunBundle,
    RunManifest,
    RunStatus,
    RunSummary,
    utc_now,
)
from opencompete.persistence.models import ArtifactSnapshot, PublishedRun
from opencompete.security import environment_secret_values, redact_text

_BUNDLE_NAME = "bundle.json"
_REPORT_NAME = "report.md"
_MANIFEST_NAME = "manifest.json"
_EVENTS_NAME = "events.jsonl"
_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "access_token",
    "refresh_token",
    "secret",
}


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _json_bytes(model: RunBundle | RunManifest) -> bytes:
    payload = json.dumps(
        model.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    return f"{payload}\n".encode()


def _normalize_key(key: object) -> str:
    return str(key).casefold().replace("-", "_").replace(" ", "_")


def _is_sensitive_key(key: object) -> bool:
    normalized = _normalize_key(key)
    return (
        normalized in _SENSITIVE_KEYS
        or "apikey" in normalized
        or normalized.endswith("api_key")
        or normalized.endswith("_secret")
        or normalized.endswith("_token")
        or "authorization" in normalized
    )


def _contains_sensitive_key(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(
            _is_sensitive_key(key) or _contains_sensitive_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list | tuple):
        return any(_contains_sensitive_key(item) for item in value)
    return False


def _sanitize_event(value: object, secrets: Iterable[str]) -> object:
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, item in value.items():
            serialized_key = str(key)
            if _is_sensitive_key(key):
                result[serialized_key] = "[REDACTED]"
            else:
                result[serialized_key] = _sanitize_event(item, secrets)
        return result
    if isinstance(value, list | tuple):
        return [_sanitize_event(item, secrets) for item in value]
    if isinstance(value, str):
        return redact_text(value, secrets)
    if value is None or isinstance(value, bool | int | float):
        return value
    return redact_text(str(value), secrets)


class RunRepository:
    """Publish immutable run revisions with Manifest as the final index."""

    def __init__(self, data_root: str | Path) -> None:
        """绑定包含 runs 目录的 OpenCompete 本地数据根目录."""
        self.data_root = Path(data_root)
        self.runs_root = self.data_root / "runs"
        self._event_lock = threading.Lock()

    def initialize(self, manifest: RunManifest) -> RunSummary:
        """Create one run directory and its initial unpublished manifest."""
        run_dir = self._run_dir(manifest.run_id)
        manifest_path = run_dir / _MANIFEST_NAME
        if manifest_path.exists():
            raise PersistenceError(f"任务已存在：{manifest.run_id}")
        if manifest.file_hashes:
            raise PersistenceError("初始 Manifest 不能预先声明业务文件哈希。")
        self._validate_no_secret_keys(manifest)
        try:
            (run_dir / "evaluations").mkdir(parents=True, exist_ok=True)
            self._atomic_write(manifest_path, _json_bytes(manifest))
        except OSError as exc:
            raise PersistenceError(
                f"初始化任务产物目录失败：{manifest.run_id}"
            ) from exc
        return self._summary(manifest)

    def delete_run(self, run_id: str) -> None:
        """彻底删除一个任务的本地业务产物目录（不可恢复）。.

        仅由用户显式删除请求触发；Checkpoint thread 与索引层的清理
        由调用方分别执行，本方法只负责 ``runs/<run_id>/`` 目录。
        """
        run_dir = self._run_dir(run_id)
        try:
            if run_dir.exists():
                shutil.rmtree(run_dir)
        except OSError as exc:
            raise PersistenceError(f"删除任务产物目录失败：{run_id}") from exc

    def publish(
        self,
        bundle: RunBundle,
        manifest: RunManifest,
    ) -> PublishedRun:
        """Atomically replace Bundle and Report, then publish Manifest last."""
        self._validate_publish_contract(bundle, manifest)
        self._validate_no_secret_keys(manifest)
        run_dir = self._run_dir(manifest.run_id)
        if not (run_dir / _MANIFEST_NAME).exists():
            raise PersistenceError(f"任务尚未初始化：{manifest.run_id}")
        current = self.load_manifest(manifest.run_id)
        if current.scope_revision > manifest.scope_revision:
            raise PersistenceError("Scope revision 不能回退。")
        if manifest.artifact_revision < current.artifact_revision:
            raise PersistenceError("artifact revision 不能回退。")
        if current.file_hashes and (
            manifest.artifact_revision == current.artifact_revision
        ):
            raise PersistenceError("已发布的 artifact revision 不能被覆盖。")

        bundle_bytes = _json_bytes(bundle)
        report_bytes = bundle.report.markdown.encode("utf-8")
        self._reject_persisted_secrets(bundle_bytes, report_bytes)
        hashes = {
            _BUNDLE_NAME: _sha256(bundle_bytes),
            _REPORT_NAME: _sha256(report_bytes),
        }
        published_manifest = self._manifest_with_hashes(manifest, hashes)
        try:
            self._atomic_write(run_dir / _BUNDLE_NAME, bundle_bytes)
            self._atomic_write(run_dir / _REPORT_NAME, report_bytes)
            self._atomic_write(
                run_dir / _MANIFEST_NAME,
                _json_bytes(published_manifest),
            )
        except OSError as exc:
            raise PersistenceError(
                f"发布任务业务产物失败：{manifest.run_id}"
            ) from exc
        return PublishedRun(
            manifest=published_manifest,
            bundle=bundle,
            report=bundle.report.markdown,
        )

    def update_manifest(self, manifest: RunManifest) -> RunSummary:
        """更新非发布状态，终态业务结果仍必须通过 publish 写入。."""
        if manifest.status in {RunStatus.COMPLETED, RunStatus.PARTIAL}:
            raise PersistenceError("completed 或 partial 必须通过 publish 发布。")
        if manifest.file_hashes:
            raise PersistenceError("非发布状态的 Manifest 不能声明业务文件哈希。")
        self._validate_no_secret_keys(manifest)
        run_dir = self._run_dir(manifest.run_id)
        manifest_path = run_dir / _MANIFEST_NAME
        try:
            current = RunManifest.model_validate_json(manifest_path.read_bytes())
        except (OSError, ValidationError) as exc:
            raise PersistenceError(f"任务尚未初始化：{manifest.run_id}") from exc
        if manifest.scope_revision < current.scope_revision:
            raise PersistenceError("Scope revision 不能回退。")
        if manifest.artifact_revision < current.artifact_revision:
            raise PersistenceError("artifact revision 不能回退。")
        updated = manifest.model_copy(update={"updated_at": utc_now()})
        try:
            self._atomic_write(manifest_path, _json_bytes(updated))
        except OSError as exc:
            raise PersistenceError(f"更新任务状态失败：{manifest.run_id}") from exc
        return self._summary(updated)

    def rebuild_manifest_index(self, manifest: RunManifest) -> RunSummary:
        """仅在现有索引不可读时写入由 Checkpoint 重建的非发布索引。."""
        if manifest.status in {RunStatus.COMPLETED, RunStatus.PARTIAL}:
            raise PersistenceError("重建的 Manifest 不能冒充已发布终态。")
        if manifest.file_hashes:
            raise PersistenceError("重建的 Manifest 不能信任既有业务文件哈希。")
        self._validate_no_secret_keys(manifest)
        run_dir = self._run_dir(manifest.run_id)
        manifest_path = run_dir / _MANIFEST_NAME
        try:
            RunManifest.model_validate_json(manifest_path.read_bytes())
        except (OSError, ValidationError):
            pass
        else:
            raise PersistenceError("Manifest 仍可读取，拒绝用恢复索引覆盖。")
        recovered = manifest.model_copy(update={"updated_at": utc_now()})
        try:
            (run_dir / "evaluations").mkdir(parents=True, exist_ok=True)
            self._atomic_write(manifest_path, _json_bytes(recovered))
        except OSError as exc:
            raise PersistenceError(
                f"从 Checkpoint 重建 Manifest 失败：{manifest.run_id}"
            ) from exc
        return self._summary(recovered)

    def load_verified(self, run_id: str) -> PublishedRun:
        """Load only a fully published revision whose hashes and DTOs agree."""
        run_dir = self._run_dir(run_id)
        try:
            manifest = RunManifest.model_validate_json(
                (run_dir / _MANIFEST_NAME).read_bytes()
            )
            bundle_bytes = (run_dir / _BUNDLE_NAME).read_bytes()
            report_bytes = (run_dir / _REPORT_NAME).read_bytes()
            bundle = RunBundle.model_validate_json(bundle_bytes)
        except (OSError, ValidationError) as exc:
            raise PersistenceError(f"任务业务产物无法读取：{run_id}") from exc

        self._validate_publish_contract(bundle, manifest)
        expected = manifest.file_hashes
        if (
            expected.get(_BUNDLE_NAME) != _sha256(bundle_bytes)
            or expected.get(_REPORT_NAME) != _sha256(report_bytes)
        ):
            raise PersistenceError(f"任务业务产物哈希校验失败：{run_id}")
        try:
            report = report_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PersistenceError(f"任务报告不是有效 UTF-8：{run_id}") from exc
        if report != bundle.report.markdown:
            raise PersistenceError(f"任务报告与 Bundle 内容不一致：{run_id}")
        return PublishedRun(manifest=manifest, bundle=bundle, report=report)

    def load_manifest(self, run_id: str) -> RunManifest:
        """读取任务状态索引，不要求终态业务产物已经发布。."""
        try:
            return RunManifest.model_validate_json(
                (self._run_dir(run_id) / _MANIFEST_NAME).read_bytes()
            )
        except (OSError, ValidationError) as exc:
            raise PersistenceError(f"任务不存在或 Manifest 无法读取：{run_id}") from exc

    def inspect(self, run_id: str) -> ArtifactSnapshot | None:
        """Inspect artifact validity without mutating either truth source."""
        run_dir = self._run_dir(run_id)
        manifest_path = run_dir / _MANIFEST_NAME
        bundle_path = run_dir / _BUNDLE_NAME
        report_path = run_dir / _REPORT_NAME
        if not any(path.exists() for path in (manifest_path, bundle_path, report_path)):
            return None

        manifest: RunManifest | None = None
        bundle: RunBundle | None = None
        bundle_bytes: bytes | None = None
        report_bytes: bytes | None = None
        try:
            manifest = RunManifest.model_validate_json(manifest_path.read_bytes())
        except (OSError, ValidationError):
            pass
        try:
            bundle_bytes = bundle_path.read_bytes()
            bundle = RunBundle.model_validate_json(bundle_bytes)
        except (OSError, ValidationError):
            pass
        try:
            report_bytes = report_path.read_bytes()
        except OSError:
            pass

        source = manifest or bundle
        scope_revision = (
            manifest.scope_revision
            if manifest is not None
            else bundle.scope.revision
            if bundle is not None
            else 0
        )
        artifact_revision = source.artifact_revision if source is not None else 0
        status = source.status.value if source is not None else "failed"
        expected = manifest.file_hashes if manifest is not None else {}
        contract_valid = False
        if manifest is not None and bundle is not None:
            try:
                self._validate_publish_contract(bundle, manifest)
                contract_valid = True
            except PersistenceError:
                pass
        bundle_valid = (
            contract_valid
            and bundle_bytes is not None
            and expected.get(_BUNDLE_NAME) == _sha256(bundle_bytes)
        )
        report_valid = (
            bundle_valid
            and report_bytes is not None
            and expected.get(_REPORT_NAME) == _sha256(report_bytes)
            and self._report_matches_bundle(report_bytes, bundle)
        )
        return ArtifactSnapshot(
            run_id=run_id,
            scope_revision=scope_revision,
            artifact_revision=artifact_revision,
            status=status,
            manifest_valid=contract_valid and bool(expected) and report_valid,
            bundle_valid=bundle_valid,
            report_valid=report_valid,
        )

    def list_runs(self) -> list[RunSummary]:
        """List parseable manifests without requiring Graph or SQLite access."""
        if not self.runs_root.exists():
            return []
        summaries: list[RunSummary] = []
        for manifest_path in self.runs_root.glob(f"*/{_MANIFEST_NAME}"):
            try:
                manifest = RunManifest.model_validate_json(
                    manifest_path.read_bytes()
                )
            except (OSError, ValidationError):
                continue
            summary = self._summary(manifest)
            if manifest.file_hashes:
                snapshot = self.inspect(manifest.run_id)
                if snapshot is None or not snapshot.is_valid:
                    summary = summary.model_copy(
                        update={"status": RunStatus.FAILED}
                    )
            summaries.append(summary)
        return sorted(summaries, key=lambda item: item.updated_at, reverse=True)

    def append_event(
        self,
        run_id: str,
        event: Mapping[str, Any],
        *,
        secrets: Iterable[str] = (),
    ) -> None:
        """Append one redacted JSON event without making it a truth source."""
        run_dir = self._run_dir(run_id)
        if not (run_dir / _MANIFEST_NAME).exists():
            raise PersistenceError(f"任务尚未初始化：{run_id}")
        all_secrets = (*secrets, *environment_secret_values())
        sanitized = _sanitize_event(event, all_secrets)
        payload = (
            json.dumps(sanitized, ensure_ascii=False, sort_keys=True) + "\n"
        ).encode("utf-8")
        try:
            with self._event_lock:
                with (run_dir / _EVENTS_NAME).open("ab") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
        except OSError as exc:
            raise PersistenceError(f"写入任务事件失败：{run_id}") from exc

    def repair_from_bundle(self, run_id: str) -> PublishedRun:
        """Rebuild a damaged Report only when the published Bundle is trusted."""
        run_dir = self._run_dir(run_id)
        try:
            manifest = RunManifest.model_validate_json(
                (run_dir / _MANIFEST_NAME).read_bytes()
            )
            bundle_bytes = (run_dir / _BUNDLE_NAME).read_bytes()
            bundle = RunBundle.model_validate_json(bundle_bytes)
        except (OSError, ValidationError) as exc:
            raise PersistenceError(f"无法读取可信 Bundle：{run_id}") from exc
        self._validate_publish_contract(bundle, manifest)
        if manifest.file_hashes.get(_BUNDLE_NAME) != _sha256(bundle_bytes):
            raise PersistenceError(f"Bundle 哈希无效，拒绝自动修复：{run_id}")

        report_bytes = bundle.report.markdown.encode("utf-8")
        hashes = {
            _BUNDLE_NAME: _sha256(bundle_bytes),
            _REPORT_NAME: _sha256(report_bytes),
        }
        repaired_manifest = self._manifest_with_hashes(manifest, hashes)
        try:
            self._atomic_write(run_dir / _REPORT_NAME, report_bytes)
            self._atomic_write(
                run_dir / _MANIFEST_NAME,
                _json_bytes(repaired_manifest),
            )
        except OSError as exc:
            raise PersistenceError(f"修复任务报告失败：{run_id}") from exc
        return PublishedRun(
            manifest=repaired_manifest,
            bundle=bundle,
            report=bundle.report.markdown,
        )

    def _run_dir(self, run_id: str) -> Path:
        if not run_id or run_id in {".", ".."}:
            raise PersistenceError("run_id 不能为空或路径片段。")
        if Path(run_id).name != run_id or "/" in run_id or "\\" in run_id:
            raise PersistenceError("run_id 不能包含路径分隔符。")
        return self.runs_root / run_id

    def _validate_publish_contract(
        self,
        bundle: RunBundle,
        manifest: RunManifest,
    ) -> None:
        if bundle.run_id != manifest.run_id:
            raise PersistenceError("Bundle 与 Manifest 的 run_id 不一致。")
        if bundle.scope.revision != manifest.scope_revision:
            raise PersistenceError("Bundle 与 Manifest 的 Scope revision 不一致。")
        if bundle.artifact_revision != manifest.artifact_revision:
            raise PersistenceError("Bundle 与 Manifest 的 artifact revision 不一致。")
        if bundle.status is not manifest.status:
            raise PersistenceError("Bundle 与 Manifest 的状态不一致。")
        if bundle.budget != manifest.budget_summary:
            raise PersistenceError("Bundle 与 Manifest 的预算摘要不一致。")

    def _validate_no_secret_keys(self, manifest: RunManifest) -> None:
        if _contains_sensitive_key(manifest.model_dump(mode="python")):
            raise PersistenceError("Manifest 包含禁止持久化的密钥字段。")
        self._reject_persisted_secrets(_json_bytes(manifest))

    def _reject_persisted_secrets(self, *payloads: bytes) -> None:
        for secret in environment_secret_values():
            encoded = secret.encode("utf-8")
            if any(encoded in payload for payload in payloads):
                raise PersistenceError("业务产物包含环境变量中的秘密，拒绝写入。")

    def _manifest_with_hashes(
        self,
        manifest: RunManifest,
        hashes: dict[str, str],
    ) -> RunManifest:
        data = manifest.model_dump(mode="python")
        data["file_hashes"] = hashes
        data["updated_at"] = utc_now()
        return RunManifest.model_validate(data)

    def _report_matches_bundle(
        self,
        report_bytes: bytes,
        bundle: RunBundle | None,
    ) -> bool:
        if bundle is None:
            return False
        try:
            return report_bytes.decode("utf-8") == bundle.report.markdown
        except UnicodeDecodeError:
            return False

    def _summary(self, manifest: RunManifest) -> RunSummary:
        return RunSummary(
            run_id=manifest.run_id,
            status=manifest.status,
            scope_revision=manifest.scope_revision,
            artifact_revision=manifest.artifact_revision,
            updated_at=manifest.updated_at,
        )

    def _atomic_write(self, path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, path)
        except BaseException:
            temporary_path.unlink(missing_ok=True)
            raise
