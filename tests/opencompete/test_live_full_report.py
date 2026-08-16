"""真实供应商完整报告闭环；仅在显式选择 live marker 时运行。"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest
from openai import (
    APIConnectionError,
    APIResponseValidationError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
)

from opencompete.application import ApplicationRunner
from opencompete.models import (
    AnalysisLevel,
    CandidateChoice,
    CandidateSelection,
    ConfirmationKind,
    DiscoveryCandidate,
    ResearchEntity,
    ResearchRequest,
    RunStatus,
    ScopeConfirmation,
)
from opencompete.security import sanitized_error
from opencompete.settings import OpenCompeteSettings, load_settings

_REQUIRED_SECRETS = (
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_MODEL",
    "TAVILY_API_KEY",
)
_MUTATED_ENVIRONMENT = (
    *_REQUIRED_SECRETS,
    "GET_API_KEYS_FROM_CONFIG",
    "LANGSMITH_TRACING",
)
_ATTEMPT_FIELDS = {
    "execution_id",
    "node",
    "unit_id",
    "provider_kind",
    "call_id",
    "attempt_id",
    "attempt_number",
    "retry_number",
    "replayed",
}
_MODEL_PATTERN = re.compile(r"(?:openai:)?[A-Za-z0-9][A-Za-z0-9._/-]{0,127}\Z")
_CREDENTIAL_PATTERN = re.compile(
    r"(?i)(?:\A(?:openai:)?(?:sk|pk|rk)-|api[_-]?key|access[_-]?token|authorization|bearer|secret)"
)
_AUTHORIZATION_PATTERN = re.compile(
    rb"(?i)authorization[\"']?\s*[:=]\s*[\"']?(?:bearer|basic)\s+"
)
_KNOWN_LIVE_CANDIDATES = (
    ("cursor", "Cursor"),
    ("windsurf", "Windsurf"),
    ("codeium", "Codeium"),
    ("claude code", "Claude Code"),
    ("gemini code assist", "Gemini Code Assist"),
    ("amazon q developer", "Amazon Q Developer"),
)


class _ProbeFailure(RuntimeError):
    """只携带安全分类和可选状态码的端点探测失败。"""


def _normalize_model(raw_model: str) -> tuple[str, str]:
    value = raw_model.strip()
    if (
        not value
        or len(value) > 135
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        or "://" in value
        or _CREDENTIAL_PATTERN.search(value)
        or not _MODEL_PATTERN.fullmatch(value)
    ):
        raise _ProbeFailure("模型名称格式无效")
    configured = value if value.startswith("openai:") else f"openai:{value}"
    return configured, configured.removeprefix("openai:")


def _base_url_candidates(raw_base_url: str) -> tuple[str, ...]:
    value = raw_base_url.strip().rstrip("/")
    parsed = urlsplit(value)
    if (
        not value
        or len(value) > 2048
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        or parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise _ProbeFailure("Base URL 格式无效")
    candidates = [value]
    if not parsed.path.rstrip("/").endswith("/v1"):
        candidates.append(f"{value}/v1")
    return tuple(dict.fromkeys(candidates))


def _safe_probe_failure(category: str, status_code: int | None = None) -> _ProbeFailure:
    suffix = f"（HTTP {status_code}）" if status_code is not None else ""
    return _ProbeFailure(f"模型端点探测失败：{category}{suffix}")


async def _probe_models(api_key: str, candidate: str) -> bool:
    try:
        async with AsyncOpenAI(
            api_key=api_key,
            base_url=candidate,
            timeout=15.0,
            max_retries=0,
        ) as client:
            await client.models.list()
    except APIStatusError as exc:
        if exc.status_code in {401, 403}:
            # 用户可能只提供供应商根路径；同主机的 /v1 候选仍需探测。
            return False
        if exc.status_code in {404, 405}:
            return False
        raise _safe_probe_failure("服务返回错误", exc.status_code) from None
    except (APIResponseValidationError, json.JSONDecodeError, UnicodeDecodeError):
        return False
    except APITimeoutError:
        raise _safe_probe_failure("请求超时") from None
    except APIConnectionError:
        raise _safe_probe_failure("连接失败") from None
    except Exception:
        raise _safe_probe_failure("未分类客户端错误") from None
    return True


async def _probe_one_token_chat(
    api_key: str,
    candidate: str,
    api_model: str,
) -> None:
    try:
        async with AsyncOpenAI(
            api_key=api_key,
            base_url=candidate,
            timeout=20.0,
            max_retries=0,
        ) as client:
            await client.chat.completions.create(
                model=api_model,
                messages=[{"role": "user", "content": "Reply OK."}],
                max_tokens=1,
            )
    except APIStatusError as exc:
        category = (
            "认证或权限拒绝"
            if exc.status_code in {401, 403}
            else "Chat 探测返回错误"
        )
        raise _safe_probe_failure(category, exc.status_code) from None
    except APITimeoutError:
        raise _safe_probe_failure("Chat 探测超时") from None
    except APIConnectionError:
        raise _safe_probe_failure("Chat 探测连接失败") from None
    except Exception:
        raise _safe_probe_failure("Chat 探测未分类客户端错误") from None


async def _select_base_url(api_key: str, raw_base_url: str, api_model: str) -> str:
    candidates = _base_url_candidates(raw_base_url)
    for candidate in candidates:
        if await _probe_models(api_key, candidate):
            return candidate
    candidate = candidates[-1]
    await _probe_one_token_chat(api_key, candidate, api_model)
    return candidate


def _live_settings(base: OpenCompeteSettings, data_dir: Path, model: str) -> OpenCompeteSettings:
    return base.model_copy(
        update={
            "data_dir": data_dir,
            "research_model": model,
            "summarization_model": model,
            "compression_model": model,
            "final_report_model": model,
            "research_model_max_tokens": 4000,
            "summarization_model_max_tokens": 800,
            "compression_model_max_tokens": 800,
            "final_report_model_max_tokens": 6000,
            "artifact_extraction_model_max_tokens": 10000,
            "final_report_input_max_chars": 24000,
            # 第三方兼容模型的上下文上限未知；控制单页输入规模是传输兼容性边界，
            # 不减少 Tavily 调用、研究迭代或任务预算。
            "max_content_length": 2000,
            "tavily_max_results_per_query": 2,
            "llm_request_timeout_seconds": 300,
            "llm_sdk_max_retries": 0,
            "max_structured_output_retries": 2,
            "max_concurrent_research_units": 2,
            "max_researcher_iterations": 3,
            "max_react_tool_calls": 6,
            "max_tavily_calls": 100,
            "max_input_tokens": 2000000,
            "max_output_tokens": 500000,
            "max_cost_usd": 100.0,
            "langsmith_tracing": False,
        }
    )


def _request() -> ResearchRequest:
    return ResearchRequest(
        query=(
            "发现并确认 Cursor，然后比较 GitHub Copilot 与 Cursor 的产品服务层"
            "竞争力，重点分析 capabilities 与 pricing，"
            "并形成有来源、证据和明确限制的完整报告。"
        ),
        entities=(ResearchEntity(name="GitHub Copilot"),),
        levels=(AnalysisLevel.PRODUCT_SERVICE,),
        dimensions={
            AnalysisLevel.PRODUCT_SERVICE: ("capabilities", "pricing"),
        },
        discover_candidates=True,
    )


def _select_known_live_candidate(
    candidates: Sequence[DiscoveryCandidate],
) -> tuple[DiscoveryCandidate, str]:
    """真实测试只批准可明确识别的产品，不把任意网页标题当作对象。"""
    for candidate in candidates:
        searchable = " ".join(
            (
                candidate.entity.name,
                candidate.source_title,
                candidate.source_url,
                candidate.summary or "",
            )
        ).casefold()
        for marker, canonical_name in _KNOWN_LIVE_CANDIDATES:
            if marker in searchable:
                return candidate, canonical_name
    pytest.fail("真实候选中没有可明确识别的产品，拒绝自动批准任意搜索结果")


def _restore_environment(original: dict[str, str | None]) -> None:
    for key, value in original.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def _scan_persisted_secrets(data_dir: Path, secrets: tuple[str, ...]) -> None:
    for path in data_dir.rglob("*"):
        if not path.is_file():
            continue
        payload = path.read_bytes()
        for secret in secrets:
            if secret.encode("utf-8") in payload:
                pytest.fail(f"检测到秘密落盘：{path.relative_to(data_dir)}")
        if _AUTHORIZATION_PATTERN.search(payload):
            pytest.fail(f"检测到 Authorization 模式落盘：{path.relative_to(data_dir)}")


@pytest.mark.asyncio
async def test_base_url_candidate_falls_back_after_models_parse_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """根路径返回非 JSON 成功页时只尝试一次安全的 /v1 候选。"""
    visited: list[str] = []

    class FakeModels:
        def __init__(self, base_url: str) -> None:
            self.base_url = base_url

        async def list(self) -> object:
            if self.base_url.endswith("/v1"):
                return object()
            response = httpx.Response(
                200,
                request=httpx.Request("GET", "https://provider.invalid/models"),
            )
            raise APIResponseValidationError(response=response, body="<html>")

    class FakeAsyncOpenAI:
        def __init__(self, *, base_url: str, **_: object) -> None:
            visited.append(base_url)
            self.models = FakeModels(base_url)

        async def __aenter__(self) -> FakeAsyncOpenAI:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

    monkeypatch.setattr(sys.modules[__name__], "AsyncOpenAI", FakeAsyncOpenAI)

    selected = await _select_base_url(
        "dummy-api-key",
        "https://provider.invalid",
        "gpt-test",
    )

    assert selected == "https://provider.invalid/v1"
    assert visited == ["https://provider.invalid", "https://provider.invalid/v1"]


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_full_report_end_to_end(tmp_path: Path) -> None:
    """以真实模型和 Tavily 验证完整报告、持久化、CLI 与秘密边界。"""
    original_environment = {key: os.environ.get(key) for key in _MUTATED_ENVIRONMENT}
    try:
        base_settings = load_settings()
        missing = [key for key in _REQUIRED_SECRETS if not os.environ.get(key)]
        if missing:
            pytest.skip(f"缺少 live 环境变量：{', '.join(missing)}")
        api_key = os.environ["OPENAI_API_KEY"]
        tavily_key = os.environ["TAVILY_API_KEY"]
        configured_model, api_model = _normalize_model(os.environ["OPENAI_MODEL"])
        try:
            selected_base_url = await _select_base_url(
                api_key,
                os.environ["OPENAI_BASE_URL"],
                api_model,
            )
        except _ProbeFailure as exc:
            pytest.fail(exc.args[0], pytrace=False)
        os.environ["OPENAI_BASE_URL"] = selected_base_url
        os.environ["GET_API_KEYS_FROM_CONFIG"] = "false"
        os.environ["LANGSMITH_TRACING"] = "false"

        configured_data_dir = os.environ.get("OPENCOMPETE_LIVE_DATA_DIR")
        data_dir = (
            Path(configured_data_dir)
            if configured_data_dir
            else tmp_path / "live-data"
        )
        settings = _live_settings(base_settings, data_dir, configured_model)
        resume_run_id = os.environ.get("OPENCOMPETE_LIVE_RESUME_RUN_ID")
        try:
            if resume_run_id:
                run_id = resume_run_id
                published = await ApplicationRunner(settings).resume_run(run_id)
            else:
                first_runner = ApplicationRunner(settings)
                pending = await first_runner.create_run(_request())
                run_id = pending.run_id
                assert pending.status is RunStatus.AWAITING_CONFIRMATION
                assert pending.scope is not None
                assert pending.confirmation_kind is ConfirmationKind.CANDIDATE_SELECTION
                assert pending.discovery_candidates
                selected_candidate, confirmed_candidate_name = (
                    _select_known_live_candidate(pending.discovery_candidates)
                )
                scope_pending = await ApplicationRunner(settings).resume_run(
                    run_id,
                    CandidateSelection(
                        expected_revision=pending.scope.revision,
                        selected_candidates=(
                            CandidateChoice(
                                candidate_id=selected_candidate.candidate_id,
                                entity_name=confirmed_candidate_name,
                            ),
                        ),
                    ),
                )
                assert scope_pending.status is RunStatus.AWAITING_CONFIRMATION
                assert scope_pending.scope is not None
                assert (
                    scope_pending.confirmation_kind
                    is ConfirmationKind.SCOPE_CONFIRMATION
                )
                assert confirmed_candidate_name in {
                    entity.name for entity in scope_pending.scope.entities
                }
                published = await ApplicationRunner(settings).resume_run(
                    run_id,
                    ScopeConfirmation(
                        accepted=True,
                        expected_revision=scope_pending.scope.revision,
                    ),
                )
        except Exception as exc:
            error = sanitized_error(exc)
            pytest.fail(
                f"真实研究失败：{type(exc).__name__}/{error.code}",
                pytrace=False,
            )
        assert published.status in {RunStatus.COMPLETED, RunStatus.PARTIAL}
        assert published.bundle is not None
        bundle = published.bundle
        assert bundle.sources
        assert bundle.evidence
        assert bundle.claims
        assert bundle.comparisons
        assert bundle.report.markdown.strip()
        assert len(bundle.report.markdown) >= 5000
        assert bundle.report.markdown.count("## ") >= 4
        assert not any(
            identifier in bundle.report.markdown
            for identifier in (
                *(source.source_id for source in bundle.sources),
                *(item.evidence_id for item in bundle.evidence),
                *(claim.claim_id for claim in bundle.claims),
                *(comparison.comparison_id for comparison in bundle.comparisons),
            )
        )
        expected_entities = {entity.entity_id for entity in bundle.scope.entities}
        assert expected_entities <= {claim.entity_id for claim in bundle.claims}
        assert expected_entities <= {
            entity_id
            for comparison in bundle.comparisons
            for entity_id in comparison.entity_ids
        }
        assert bundle.traceability_violations() == []
        assert not any("预算" in item for item in bundle.report.limitations)
        if published.status is RunStatus.PARTIAL:
            assert bundle.report.limitations

        report_path = data_dir / "runs" / run_id / "report.md"
        assert report_path.read_text(encoding="utf-8") == bundle.report.markdown
        reopened_bundle = await ApplicationRunner(settings).load_run(run_id)
        assert reopened_bundle == bundle

        child_environment = os.environ.copy()
        child_environment.update(
            {"COLUMNS": "10000", "NO_COLOR": "1", "PYTHONUTF8": "1"}
        )
        shown = subprocess.run(
            [
                sys.executable,
                "-m",
                "opencompete.cli",
                "--data-dir",
                str(data_dir),
                "show",
                run_id,
            ],
            cwd=Path.cwd(),
            env=child_environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
            check=False,
        )
        if shown.returncode != 0:
            pytest.fail(f"CLI show 子进程失败，退出码：{shown.returncode}")
        assert run_id in shown.stdout
        assert bundle.report.markdown in shown.stdout

        events_path = data_dir / "runs" / run_id / "events.jsonl"
        events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
        attempt_events = [
            event
            for event in events
            if str(event.get("event", "")).startswith("call_attempt_")
        ]
        assert attempt_events
        assert all(_ATTEMPT_FIELDS <= event.keys() for event in attempt_events)
        successful_nodes = {
            event.get("node")
            for event in attempt_events
            if event.get("event") == "call_attempt_succeeded"
        }
        assert {
            "discovery",
            "compress_research",
            "final_report_generation",
            "materialize",
        } <= successful_nodes
        _scan_persisted_secrets(data_dir, (api_key, tavily_key))
    finally:
        _restore_environment(original_environment)
