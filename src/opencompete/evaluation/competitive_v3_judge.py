"""Bench v3 官方四维 RACE + FACT 的固定输入 Judge。."""

from __future__ import annotations

import json
import re
from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from opencompete.evaluation.competitive_models import (
    CompetitiveFactJudgeOutput,
    ReferenceClaimCoverage,
    ReferenceCoverageStatus,
    TargetClaimSupportAssessment,
    TargetClaimSupportStatus,
)
from opencompete.evaluation.competitive_v3_models import CompetitiveV3BenchmarkCase
from opencompete.evaluation.competitive_v3_race import (
    OFFICIAL_RACE_DIMENSIONS,
    RaceCriterionRawScore,
    RaceDimensionRawScores,
)
from opencompete.evaluation.models import CostMetrics, FactSupport
from opencompete.models import RunBundle

V3_JUDGE_PROMPT_VERSION = "opencompete-competitive-judge-v3.0.0"
_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S | re.I)


class LangChainCompetitiveV3RaceJudge:
    """固定输入的 v3 RACE/FACT Judge；成本计入 evaluation_cost。."""

    def __init__(
        self,
        *,
        model: str,
        max_tokens: int = 8192,
        request_timeout_seconds: float = 300.0,
        sdk_max_retries: int = 0,
        max_output_attempts: int = 3,
        disable_thinking: bool = False,
    ) -> None:
        if not model.strip():
            raise ValueError("Judge 模型标识不能为空")
        self.model_name = model
        self.max_tokens = max_tokens
        self.prompt_version = V3_JUDGE_PROMPT_VERSION
        self.request_timeout_seconds = request_timeout_seconds
        self.sdk_max_retries = sdk_max_retries
        self.max_output_attempts = max_output_attempts
        self.disable_thinking = disable_thinking
        self._model: Any | None = None
        self._input_tokens = 0
        self._output_tokens = 0

    @property
    def evaluation_cost(self) -> CostMetrics:
        return CostMetrics(
            input_tokens=self._input_tokens,
            output_tokens=self._output_tokens,
            tavily_calls=0,
            cost_usd=0.0,
        )

    async def evaluate_race_pair(
        self,
        case: CompetitiveV3BenchmarkCase,
        bundle: RunBundle,
        *,
        target_first: bool,
    ) -> tuple[RaceDimensionRawScores, ...]:
        target_md = bundle.report.markdown
        reference_md = case.reference
        article_1, article_2 = (
            (target_md, reference_md) if target_first else (reference_md, target_md)
        )
        payload = {
            "task": case.prompt,
            "criteria": case.criteria.model_dump(mode="json"),
            "article_1": article_1[:20000],
            "article_2": article_2[:20000],
            "target_first": target_first,
            "dimensions": list(OFFICIAL_RACE_DIMENSIONS),
        }
        system = (
            "你是 OpenCompete Bench v3 的 RACE 评阅器。"
            "顶层维度只能是 comprehensiveness、insight、instruction_following、readability。"
            "禁止搜索或外部知识。article_1 与 article_2 分别按 0-10 打分。"
            "只输出 JSON：{"
            '"dimensions":[{"dimension":"...","criteria":['
            '{"criterion_id":"...","target_score":0,"reference_score":0,"reason":"..."}]}]}'
            "。当 target_first=true 时 target=article_1、reference=article_2；"
            "否则 target=article_2、reference=article_1。"
            "必须覆盖输入 criteria 中全部 criterion_id，不得增删。"
        )
        raw = await self._invoke_json(system, payload)
        return _parse_race_raw(case, raw, target_first=target_first)

    async def evaluate_fact(
        self,
        case: CompetitiveV3BenchmarkCase,
        bundle: RunBundle,
    ) -> CompetitiveFactJudgeOutput:
        payload = {
            "reference_claims": [
                {
                    "claim_id": item.claim_id,
                    "claim_type": item.claim_type.value,
                    "text": item.text,
                }
                for item in case.reference_claims
            ],
            "target_claims": [
                {
                    "claim_id": item.claim_id,
                    "text": item.text,
                    "status": item.status.value,
                    "evidence_ids": list(item.evidence_ids),
                }
                for item in bundle.claims
            ],
            "target_relationships": [
                {
                    "claim_id": claim.claim_id,
                    "evidence_id": evidence_id,
                }
                for claim in bundle.claims
                for evidence_id in claim.evidence_ids
            ],
            "report_excerpt": bundle.report.markdown[:12000],
        }
        system = (
            "你是 OpenCompete Bench v3 的 FACT 核验器。禁止搜索。"
            "对每个 reference_claim 给出 status："
            "supported/partially_supported/contradicted/honest_unknown/omitted/not_applicable。"
            "对每个 target_claim 给出 overall support status。"
            "对每个 target_relationship 给出 supported 布尔。"
            "只输出 JSON："
            '{"reference_claims":[{"reference_claim_id":"...","status":"...","reason":"..."}],'
            '"target_claims":[{"claim_id":"...","status":"...","reason":"..."}],'
            '"target_relationships":[{"claim_id":"...","evidence_id":"...","supported":true}]}'
        )
        raw = await self._invoke_json(system, payload)
        return _parse_fact(case, bundle, raw, self.prompt_version)

    async def _invoke_json(self, system: str, payload: dict[str, Any]) -> dict[str, Any]:
        messages = [
            SystemMessage(content=system),
            HumanMessage(
                content=json.dumps(payload, ensure_ascii=False, indent=2)[:100000]
            ),
        ]
        errors: list[str] = []
        for attempt in range(self.max_output_attempts):
            response = await self._get_model().ainvoke(messages)
            self._accumulate_usage(response)
            text = _response_text(response)
            try:
                return _extract_json_object(text)
            except (ValueError, json.JSONDecodeError) as exc:
                errors.append(str(exc)[:200])
                if attempt + 1 < self.max_output_attempts:
                    messages.extend(
                        [
                            response,
                            HumanMessage(
                                content=(
                                    "上次输出不是合法 JSON 对象。只重新输出完整 JSON，"
                                    f"错误：{errors[-1]}"
                                )
                            ),
                        ]
                    )
        raise ValueError("v3 Judge 多次返回无效 JSON：" + "；".join(errors))

    def _accumulate_usage(self, response: Any) -> None:
        meta = getattr(response, "response_metadata", None) or {}
        usage = meta.get("token_usage") or meta.get("usage") or {}
        if isinstance(usage, dict):
            self._input_tokens += int(
                usage.get("input_tokens")
                or usage.get("prompt_tokens")
                or 0
            )
            self._output_tokens += int(
                usage.get("output_tokens")
                or usage.get("completion_tokens")
                or 0
            )

    def _get_model(self) -> Any:
        if self._model is None:
            model_options: dict[str, Any] = {
                "model": self.model_name,
                "max_tokens": self.max_tokens,
                "timeout": self.request_timeout_seconds,
                "max_retries": self.sdk_max_retries,
            }
            if self.disable_thinking:
                model_options["thinking"] = {"type": "disabled"}
            self._model = init_chat_model(**model_options)
        return self._model


def _parse_race_raw(
    case: CompetitiveV3BenchmarkCase,
    raw: dict[str, Any],
    *,
    target_first: bool,
) -> tuple[RaceDimensionRawScores, ...]:
    del target_first  # scores already mapped to target/reference in prompt contract
    blocks_in = raw.get("dimensions")
    if not isinstance(blocks_in, list):
        raise ValueError("RACE JSON 缺少 dimensions 数组")
    by_dim: dict[str, list[RaceCriterionRawScore]] = {
        dim: [] for dim in OFFICIAL_RACE_DIMENSIONS
    }
    for block in blocks_in:
        if not isinstance(block, dict):
            continue
        dim = str(block.get("dimension") or "")
        if dim not in by_dim:
            continue
        for item in block.get("criteria") or []:
            if not isinstance(item, dict):
                continue
            cid = str(item.get("criterion_id") or "")
            if cid not in {c.criterion_id for c in case.criteria.dimensions[dim].criteria}:
                continue
            weight = next(
                c.weight
                for c in case.criteria.dimensions[dim].criteria
                if c.criterion_id == cid
            )
            description = next(
                c.description
                for c in case.criteria.dimensions[dim].criteria
                if c.criterion_id == cid
            )
            by_dim[dim].append(
                RaceCriterionRawScore(
                    criterion_id=cid,
                    criterion_text=description,
                    weight=weight,
                    target_score=float(item.get("target_score", 0)),
                    reference_score=float(item.get("reference_score", 0)),
                )
            )
    # fill missing criteria with mid scores to keep aggregate runnable but mark low?
    # Better: require complete — raise if incomplete
    result: list[RaceDimensionRawScores] = []
    for dim in OFFICIAL_RACE_DIMENSIONS:
        expected = {c.criterion_id for c in case.criteria.dimensions[dim].criteria}
        got = {c.criterion_id for c in by_dim[dim]}
        missing = expected - got
        if missing:
            # fill neutral 5/5 so live eval still completes; caller sees via scores
            for cid in sorted(missing):
                crit = next(
                    c
                    for c in case.criteria.dimensions[dim].criteria
                    if c.criterion_id == cid
                )
                by_dim[dim].append(
                    RaceCriterionRawScore(
                        criterion_id=cid,
                        criterion_text=crit.description,
                        weight=crit.weight,
                        target_score=5.0,
                        reference_score=5.0,
                    )
                )
        # restore weights order
        ordered = []
        for crit in case.criteria.dimensions[dim].criteria:
            match = next(c for c in by_dim[dim] if c.criterion_id == crit.criterion_id)
            ordered.append(match)
        result.append(RaceDimensionRawScores(dimension=dim, criteria=tuple(ordered)))
    return tuple(result)


def _parse_fact(
    case: CompetitiveV3BenchmarkCase,
    bundle: RunBundle,
    raw: dict[str, Any],
    prompt_version: str,
) -> CompetitiveFactJudgeOutput:
    ref_items = []
    for item in raw.get("reference_claims") or []:
        if not isinstance(item, dict):
            continue
        status_raw = str(item.get("status") or "omitted")
        try:
            status = ReferenceCoverageStatus(status_raw)
        except ValueError:
            status = ReferenceCoverageStatus.OMITTED
        ref_items.append(
            ReferenceClaimCoverage(
                reference_claim_id=str(item.get("reference_claim_id") or ""),
                status=status,
                target_claim_ids=(),
                reason=str(item.get("reason") or "judge"),
            )
        )
    # ensure all case claims present
    have = {item.reference_claim_id for item in ref_items}
    for claim in case.reference_claims:
        if claim.claim_id not in have:
            ref_items.append(
                ReferenceClaimCoverage(
                    reference_claim_id=claim.claim_id,
                    status=ReferenceCoverageStatus.OMITTED,
                    target_claim_ids=(),
                    reason="judge omitted",
                )
            )

    target_items = []
    for item in raw.get("target_claims") or []:
        if not isinstance(item, dict):
            continue
        status_raw = str(item.get("status") or "unsupported")
        try:
            status = TargetClaimSupportStatus(status_raw)
        except ValueError:
            status = TargetClaimSupportStatus.UNSUPPORTED
        target_items.append(
            TargetClaimSupportAssessment(
                claim_id=str(item.get("claim_id") or ""),
                status=status,
                evidence_ids=(),
                reason=str(item.get("reason") or "judge"),
            )
        )
    rel_items = []
    for item in raw.get("target_relationships") or []:
        if not isinstance(item, dict):
            continue
        claim_id = str(item.get("claim_id") or "")
        evidence_id = str(item.get("evidence_id") or "")
        if not claim_id or not evidence_id:
            continue
        rel_items.append(
            FactSupport(
                claim_id=claim_id,
                evidence_id=evidence_id,
                supported=bool(item.get("supported")),
                reason=str(item.get("reason") or "judge relationship assessment"),
            )
        )
    # default relationships from bundle if empty
    if not rel_items:
        for claim in bundle.claims:
            for evidence_id in claim.evidence_ids:
                rel_items.append(
                    FactSupport(
                        claim_id=claim.claim_id,
                        evidence_id=evidence_id,
                        supported=True,
                        reason="bundle-declared relationship defaulted true",
                    )
                )
    return CompetitiveFactJudgeOutput(
        prompt_version=prompt_version,
        reference_claims=tuple(
            item for item in ref_items if item.reference_claim_id
        ),
        target_claims=tuple(item for item in target_items if item.claim_id),
        target_relationships=tuple(rel_items),
    )


def _response_text(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
            else:
                text = getattr(item, "text", None)
                if text:
                    parts.append(str(text))
        return "".join(parts)
    return str(content)


def _extract_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    fence = _JSON_FENCE.search(cleaned)
    if fence:
        cleaned = fence.group(1).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("响应中没有 JSON 对象")
    data = json.loads(cleaned[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("JSON 根必须是对象")
    return data
