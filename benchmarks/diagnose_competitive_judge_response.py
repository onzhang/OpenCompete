"""隔离调用 Bench v2 质量 Judge，并保存脱敏响应诊断。."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from opencompete.evaluation.competitive_dataset import load_competitive_dataset
from opencompete.evaluation.competitive_judge import (
    LangChainCompetitiveReportJudge,
    _project_candidate,
    _quality_candidate_payload,
    _quality_system_prompt,
    _response_text,
    _target_candidate,
    _validate_candidate_quality_output,
    build_reference_candidate,
)
from opencompete.evaluation.competitive_models import CompetitiveJudgeOutput
from opencompete.models import RunBundle


def _redact(text: str) -> str:
    """只按常见密钥形态脱敏，不读取或记录环境变量。."""
    patterns = (
        r"sk-[A-Za-z0-9_-]{16,}",
        r"sk-ant-[A-Za-z0-9_-]{16,}",
        r"(?i)(api[_-]?key\s*[:=]\s*)[^\s,}\]]+",
        r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,}\]]+",
    )
    for pattern in patterns:
        text = re.sub(pattern, r"\1<已脱敏>" if "(" in pattern else "<已脱敏>", text)
    return text


def _safe_metadata(response: Any) -> dict[str, Any]:
    """仅提取结束原因和 Token 用量白名单。."""
    response_metadata = getattr(response, "response_metadata", {}) or {}
    usage_metadata = getattr(response, "usage_metadata", {}) or {}
    return {
        "finish_reason": response_metadata.get("stop_reason")
        or response_metadata.get("finish_reason"),
        "usage": {
            key: usage_metadata.get(key)
            for key in ("input_tokens", "output_tokens", "total_tokens")
            if usage_metadata.get(key) is not None
        },
    }


async def _run(args: argparse.Namespace) -> None:
    load_dotenv(override=False)
    run_dir = args.data_dir / "runs" / args.run_id
    bundle = RunBundle.model_validate_json(
        (run_dir / "bundle.json").read_text(encoding="utf-8")
    )
    case = load_competitive_dataset(args.dataset).get_case(args.case_id)
    article_1, locator_map_1 = _project_candidate(_target_candidate(bundle))
    article_2, locator_map_2 = _project_candidate(build_reference_candidate(case))
    order_id = "diagnostic_target_first"
    messages = [
        SystemMessage(content=_quality_system_prompt()),
        HumanMessage(
            content=_quality_candidate_payload(
                case,
                article_1,
                article_2,
                input_order_id=order_id,
            )
        ),
    ]
    judge = LangChainCompetitiveReportJudge(
        model=args.model,
        max_tokens=args.max_tokens,
        max_output_attempts=1,
    )
    response = await judge._get_model().ainvoke(messages)
    raw_content = getattr(response, "content", response)
    result: dict[str, Any] = {
        "model": args.model,
        "max_tokens": args.max_tokens,
        "metadata": _safe_metadata(response),
        "response_content": _redact(
            raw_content if isinstance(raw_content, str) else json.dumps(raw_content, ensure_ascii=False)
        ),
    }
    try:
        parsed_text = _response_text(response)
        parsed = CompetitiveJudgeOutput.model_validate_json(parsed_text)
        _validate_candidate_quality_output(
            case,
            parsed,
            expected_order_id=order_id,
            article_1_ids=set(locator_map_1),
            article_2_ids=set(locator_map_2),
        )
        result["validation"] = {"valid": True}
    except (ValueError, ValidationError) as exc:
        result["validation"] = {
            "valid": False,
            "error_type": type(exc).__name__,
            "error": _redact(str(exc)),
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(  # noqa: T201
        json.dumps(
            {"output": str(args.output), **result["metadata"], **result["validation"]},
            ensure_ascii=False,
        )
    )


def main() -> None:
    """解析参数并执行一次隔离诊断。."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--max-tokens", type=int, default=16000)
    parser.add_argument("--dataset", type=Path, default=Path("benchmarks/opencompete/v2"))
    parser.add_argument(
        "--data-dir", type=Path, default=Path(".opencompete-bench-v2-data")
    )
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
