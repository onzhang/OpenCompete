"""Utility functions and helpers for the Deep Research agent."""

import asyncio
import ipaddress
import json
import logging
import os
import re
import warnings
from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Dict, List, Literal, Optional, cast
from urllib.parse import parse_qsl, urlsplit, urlunsplit

import aiohttp
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    MessageLikeRepresentation,
    SystemMessage,
    filter_messages,
)
from langchain_core.runnables import RunnableConfig, RunnableLambda
from langchain_core.tools import (
    BaseTool,
    InjectedToolArg,
    StructuredTool,
    ToolException,
    tool,
)
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.config import get_store
from mcp import McpError
from tavily import AsyncTavilyClient

from open_deep_research.configuration import Configuration, SearchAPI
from open_deep_research.model_routing import (
    build_resilient_model,
    route_metadata,
)
from open_deep_research.prompts import summarize_webpage_prompt
from open_deep_research.state import ResearchComplete, Summary

##########################
# Tavily Search Tool Utils
##########################
TAVILY_SEARCH_DESCRIPTION = (
    "A search engine optimized for comprehensive, accurate, and trusted results. "
    "Useful for when you need to answer questions about current events."
)
TAVILY_EXTRACT_DESCRIPTION = (
    "Extract a short, verifiable excerpt from one or more already selected public "
    "HTTP/HTTPS URLs. Use basic depth unless advanced extraction is explicitly needed."
)
TAVILY_EXTRACT_MAX_URLS_PER_REQUEST = 20
TAVILY_SEARCH_SNIPPET_MAX_CHARS = 2000
TAVILY_SEARCH_TITLE_MAX_CHARS = 500
TAVILY_SUMMARY_FAILURE_MARKER = "[TAVILY_SUMMARY_UNAVAILABLE]"
RESEARCH_EVIDENCE_ARTIFACT_TYPE = "opencompete_research_evidence"
RESEARCH_EVIDENCE_ARTIFACT_VERSION = 1
_EXTRACT_CONTRACT_TYPE = "tavily_extract"
_FORBIDDEN_HOST_SUFFIXES = (
    ".internal",
    ".invalid",
    ".lan",
    ".local",
    ".localhost",
    ".test",
)
_SENSITIVE_QUERY_MARKERS = (
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "password",
    "secret",
    "signature",
    "token",
)
_SECRET_ENV_MARKERS = (
    "api_key",
    "apikey",
    "authorization",
    "password",
    "secret",
    "token",
)
_NON_SECRET_CONTROL_VALUES = {
    "0",
    "1",
    "false",
    "no",
    "none",
    "null",
    "true",
    "yes",
}
_AUTHORIZATION_PATTERN = re.compile(r"(?im)(authorization\s*[:=]\s*)[^\r\n]+")
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_SECRET_FIELD_PATTERN = re.compile(
    r"(?i)((?:api[-_ ]?key|access[-_ ]?token|password|secret)\s*[:=]\s*)[^\s,;]+"
)
_PUBLIC_HOST_LABEL_PATTERN = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


@tool(
    description=TAVILY_SEARCH_DESCRIPTION,
    response_format="content_and_artifact",
)
async def tavily_search(
    queries: List[str],
    max_results: Annotated[int, InjectedToolArg] = 5,
    topic: Annotated[Literal["general", "news", "finance"], InjectedToolArg] = "general",
    config: RunnableConfig = None
) -> tuple[str, dict[str, Any]]:
    """Fetch and summarize search results from Tavily search API.

    Args:
        queries: List of search queries to execute
        max_results: Maximum number of results to return per query
        topic: Topic filter for search results (general, news, or finance)
        config: Runtime configuration for API keys and model settings

    Returns:
        Formatted text plus a bounded structured evidence artifact.
    """
    configurable = Configuration.from_runnable_config(config)
    admitted_queries = list(queries[: configurable.tavily_max_queries_per_call])
    requested_query_count = len(queries)

    # Step 1: Execute search queries asynchronously
    search_results = await tavily_search_async(
        admitted_queries,
        max_results=min(
            max_results,
            configurable.tavily_max_results_per_query,
        ),
        topic=topic,
        include_raw_content=True,
        config=config
    )
    
    # Step 2: Deduplicate results by URL to avoid processing the same content multiple times
    unique_results = {}
    for response in search_results:
        for result in response['results']:
            url = result['url']
            if url not in unique_results:
                unique_results[url] = {**result, "query": response['query']}
    
    # Step 3: Set up the summarization model with configuration
    # Character limit to stay within model token limits (configurable)
    max_char_to_include = configurable.max_content_length
    
    # Initialize summarization model with retry logic
    model_api_key = get_api_key_for_model(
        configurable.summarization_model,
        config,
        connection_role="primary",
    )
    fallback_model = configurable.fallback_model
    fallback_attempts = (
        configurable.fallback_model_attempts if fallback_model is not None else 0
    )
    primary_options: dict[str, Any] = {
        "model": configurable.summarization_model,
        "max_tokens": configurable.summarization_model_max_tokens,
        "api_key": model_api_key,
        "base_url": get_base_url_for_model(
            configurable.summarization_model,
            config,
            connection_role="primary",
        ),
        "timeout": configurable.llm_request_timeout_seconds,
        "max_retries": configurable.llm_sdk_max_retries,
        "tags": ["langsmith:nostream"],
        "metadata": route_metadata(
            provider=configurable.primary_provider_name,
            model=configurable.summarization_model,
            fallback=False,
            route_attempts=configurable.primary_model_attempts,
            primary_attempts=configurable.primary_model_attempts,
            fallback_attempts=fallback_attempts,
            retry_delays_seconds=configurable.model_retry_delays_seconds,
            fallback_provider=(
                configurable.fallback_provider_name
                if fallback_model is not None
                else None
            ),
            fallback_model=fallback_model,
            connection_role="primary",
        ),
    }
    primary_thinking = configurable.model_thinking_by_model.get(
        configurable.summarization_model,
        configurable.model_thinking,
    )
    if primary_thinking is not None:
        primary_options["thinking"] = primary_thinking
    fallback_options: dict[str, Any] | None = None
    if fallback_model is not None:
        fallback_options = {
            "model": fallback_model,
            "max_tokens": configurable.summarization_model_max_tokens,
            "api_key": get_api_key_for_model(
                fallback_model,
                config,
                connection_role="fallback",
            ),
            "base_url": get_base_url_for_model(
                fallback_model,
                config,
                connection_role="fallback",
            ),
            "timeout": configurable.llm_request_timeout_seconds,
            "max_retries": configurable.llm_sdk_max_retries,
            "tags": ["langsmith:nostream"],
            "metadata": route_metadata(
                provider=configurable.fallback_provider_name,
                model=fallback_model,
                fallback=True,
                route_attempts=fallback_attempts,
                primary_attempts=configurable.primary_model_attempts,
                fallback_attempts=fallback_attempts,
                retry_delays_seconds=configurable.model_retry_delays_seconds,
                connection_role="fallback",
            ),
        }
        if configurable.fallback_disable_thinking:
            fallback_options["thinking"] = {"type": "disabled"}
        else:
            fallback_thinking = configurable.model_thinking_by_model.get(
                fallback_model,
                configurable.model_thinking,
            )
            if fallback_thinking is not None:
                fallback_options["thinking"] = fallback_thinking
    summarization_model = build_resilient_model(
        init_chat_model(
            configurable_fields=(
                "model",
                "max_tokens",
                "api_key",
                "base_url",
                "timeout",
                "max_retries",
                "thinking",
            ),
        ),
        prepare=lambda runnable: runnable.with_structured_output(Summary),
        primary_config=cast(RunnableConfig, primary_options),
        primary_attempts=configurable.primary_model_attempts,
        fallback_config=(
            cast(RunnableConfig, fallback_options)
            if fallback_options is not None
            else None
        ),
        fallback_attempts=fallback_attempts,
        retry_delays_seconds=configurable.model_retry_delays_seconds,
    )
    
    # Step 4: Summarize non-empty pages with the framework concurrency boundary.
    summaries: list[str | None] = [None] * len(unique_results)
    indexed_contents = [
        (index, result["raw_content"][:max_char_to_include])
        for index, result in enumerate(unique_results.values())
        if result.get("raw_content")
    ]
    if indexed_contents:
        async def summarize_content(content: str) -> str:
            return await summarize_webpage(summarization_model, content)

        summarizer = RunnableLambda(summarize_content)
        completed = await summarizer.abatch(
            [content for _, content in indexed_contents],
            config={
                "max_concurrency": configurable.max_concurrent_summarizations,
            },
        )
        for (index, _), summary in zip(indexed_contents, completed):
            summaries[index] = summary
    
    # Step 6: Combine results with their summaries
    summarized_results = {
        url: {
            'title': _safe_search_title(result.get('title')),
            'query': _safe_output_query(str(result.get('query', ''))),
            'content': _safe_search_snippet(
                result.get('content') if summary is None else summary
            ),
        }
        for url, result, summary in zip(
            unique_results.keys(), 
            unique_results.values(), 
            summaries
        )
    }
    
    # Step 7: Format the final output
    admission = (
        "QUERY_ADMISSION: "
        f"requested={requested_query_count}; "
        f"sent={len(admitted_queries)}; "
        f"truncated={requested_query_count - len(admitted_queries)}\n\n"
    )
    if not summarized_results:
        return (
            admission
            + "No valid search results found. Please try different search queries "
            "or use a different search API.",
            _research_evidence_artifact([]),
        )
    
    formatted_output = "Search results: \n\n" + admission
    for i, (url, result) in enumerate(summarized_results.items()):
        formatted_output += f"\n\n--- SOURCE {i+1}: {result['title']} ---\n"
        formatted_output += f"URL: {url}\n"
        formatted_output += f"QUERY: {result['query']}\n\n"
        formatted_output += f"SUMMARY:\n{result['content']}\n\n"
        formatted_output += "\n\n" + "-" * 80 + "\n"
    
    evidence_records: list[dict[str, Any]] = []
    for url, result in summarized_results.items():
        if not isinstance(url, str):
            continue
        try:
            safe_url = _validate_public_http_url(url)
        except ValueError:
            continue
        summary = str(result["content"]).strip()
        if not summary:
            continue
        evidence_records.append(
            {
                "query": result["query"],
                "status": "success",
                "summary": summary,
                "title": result["title"] or safe_url,
                "type": "tavily_search",
                "url": safe_url,
                "version": 1,
            }
        )

    return (
        (
            "<untrusted_search_results>\n"
            f"{formatted_output}"
            "</untrusted_search_results>"
        ),
        _research_evidence_artifact(evidence_records),
    )


@tool(
    description=TAVILY_EXTRACT_DESCRIPTION,
    response_format="content_and_artifact",
)
async def tavily_extract(
    urls: List[str],
    query: str,
    extract_depth: Literal["basic", "advanced"] = "basic",
    config: RunnableConfig = None,
) -> tuple[str, dict[str, Any]]:
    """Return bounded, source-located excerpts for selected public URLs.

    Args:
        urls: Public HTTP/HTTPS URLs selected during research.
        query: Source research query used to choose the relevant excerpt.
        extract_depth: Official Tavily extraction depth; basic is the default.
        config: Runtime configuration for the API key and excerpt bound.

    Returns:
        Versioned JSON-lines text plus the same bounded records as an artifact.
    """
    source_query = query.strip()
    if not source_query:
        raise ValueError("Tavily Extract requires a non-empty source query")
    normalized_urls = validated_tavily_extract_urls(urls)
    configurable = Configuration.from_runnable_config(config)
    batch_responses = await tavily_extract_async(
        normalized_urls,
        extract_depth=extract_depth,
        config=config,
    )

    records: list[dict[str, Any]] = []
    safe_query = _redact_sensitive_text(source_query)[:1000]
    for batch_index, (batch_urls, response) in enumerate(batch_responses, start=1):
        request_id = _safe_request_id(response.get("request_id"))
        usage = _safe_extract_usage(response.get("usage"))
        successful = _successful_extract_results(response, batch_urls)
        failed_urls = _failed_extract_urls(response, batch_urls)
        for url in batch_urls:
            result = successful.get(url)
            raw_content = result.get("raw_content") if result is not None else None
            if isinstance(raw_content, str) and raw_content:
                excerpt, char_start, char_end = _extract_excerpt(
                    raw_content,
                    source_query,
                    configurable.tavily_extract_excerpt_max_chars,
                )
                safe_excerpt = _redact_sensitive_text(excerpt)[
                    : configurable.tavily_extract_excerpt_max_chars
                ]
                records.append(
                    {
                        "batch_index": batch_index,
                        "char_end": char_end,
                        "char_start": char_start,
                        "excerpt": safe_excerpt,
                        "excerpt_redacted": safe_excerpt != excerpt,
                        "query": safe_query,
                        "request_id": request_id,
                        "status": "success",
                        "type": _EXTRACT_CONTRACT_TYPE,
                        "url": url,
                        "usage": usage,
                        "version": 1,
                    }
                )
                continue
            error_type = (
                "extract_failed"
                if url in failed_urls
                else "missing_or_empty_result"
            )
            records.append(
                {
                    "batch_index": batch_index,
                    "error_type": error_type,
                    "query": safe_query,
                    "request_id": request_id,
                    "status": "failed",
                    "type": _EXTRACT_CONTRACT_TYPE,
                    "url": url,
                    "usage": usage,
                    "version": 1,
                }
            )

    payload = "\n".join(
        json.dumps(
            record,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        for record in records
    )
    return (
        (
            "<untrusted_extract_results>\n"
            "TAVILY_EXTRACT_JSONL_V1\n"
            f"{payload}\n"
            "</untrusted_extract_results>"
        ),
        _research_evidence_artifact(records),
    )


async def tavily_extract_async(
    urls: List[str],
    *,
    extract_depth: Literal["basic", "advanced"] = "basic",
    config: RunnableConfig = None,
) -> list[tuple[list[str], dict[str, Any]]]:
    """Call the official Extract client in deterministic batches of twenty URLs."""
    tavily_client = AsyncTavilyClient(api_key=get_tavily_api_key(config))
    responses: list[tuple[list[str], dict[str, Any]]] = []
    for offset in range(0, len(urls), TAVILY_EXTRACT_MAX_URLS_PER_REQUEST):
        batch = urls[offset : offset + TAVILY_EXTRACT_MAX_URLS_PER_REQUEST]
        response = await tavily_client.extract(
            urls=batch,
            include_images=False,
            extract_depth=extract_depth,
            format="markdown",
            include_usage=True,
        )
        if not isinstance(response, dict):
            raise TypeError("Tavily Extract returned an invalid response")
        responses.append((batch, response))
    return responses


def validated_tavily_extract_urls(urls: List[str]) -> list[str]:
    """Validate and deduplicate the URLs admitted to official Tavily Extract."""
    if not urls:
        raise ValueError("Tavily Extract requires at least one URL")
    normalized = [_validate_public_http_url(url) for url in urls]
    return list(dict.fromkeys(normalized))


def _validate_public_http_url(url: str) -> str:
    value = url.strip()
    if len(value) > 4096:
        raise ValueError("URL is too long")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("URL is invalid") from exc
    scheme = parsed.scheme.casefold()
    if scheme not in {"http", "https"}:
        raise ValueError("Only public HTTP/HTTPS URLs are allowed")
    if not parsed.hostname:
        raise ValueError("A public URL must include a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URLs containing credentials are not allowed")

    host = _validate_public_host(parsed.hostname.rstrip(".").casefold())
    for key, _ in parse_qsl(parsed.query, keep_blank_values=True):
        normalized_key = key.casefold().replace("-", "_")
        if any(marker in normalized_key for marker in _SENSITIVE_QUERY_MARKERS):
            raise ValueError("URLs containing secret query parameters are not allowed")
    if any(secret in value for secret in _environment_secret_values()):
        raise ValueError("URLs containing process secrets are not allowed")

    serialized_host = f"[{host}]" if ":" in host else host
    default_port = (scheme == "http" and port == 80) or (
        scheme == "https" and port == 443
    )
    netloc = (
        serialized_host
        if port is None or default_port
        else f"{serialized_host}:{port}"
    )
    return urlunsplit((scheme, netloc, parsed.path, parsed.query, ""))


def _validate_public_host(host: str) -> str:
    if host == "localhost" or host.endswith(_FORBIDDEN_HOST_SUFFIXES):
        raise ValueError("Local or internal hostnames are not allowed")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return _validate_public_hostname(host)
    if not address.is_global:
        raise ValueError("Non-public IP addresses are not allowed")
    return address.compressed


def _validate_public_hostname(host: str) -> str:
    try:
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("Hostname is invalid") from exc
    labels = ascii_host.split(".")
    if len(ascii_host) > 253 or len(labels) < 2:
        raise ValueError("A fully qualified public hostname is required")
    if all(label.isdigit() for label in labels):
        raise ValueError("Non-standard numeric IP forms are not allowed")
    if any(not _PUBLIC_HOST_LABEL_PATTERN.fullmatch(label) for label in labels):
        raise ValueError("Hostname is invalid")
    return ascii_host


def _successful_extract_results(
    response: dict[str, Any],
    batch_urls: List[str],
) -> dict[str, dict[str, Any]]:
    allowed = set(batch_urls)
    results: dict[str, dict[str, Any]] = {}
    values = response.get("results")
    if not isinstance(values, list):
        return results
    for item in values:
        if not isinstance(item, dict) or not isinstance(item.get("url"), str):
            continue
        try:
            url = _validate_public_http_url(item["url"])
        except ValueError:
            continue
        if url in allowed and url not in results:
            results[url] = item
    return results


def _failed_extract_urls(
    response: dict[str, Any],
    batch_urls: List[str],
) -> set[str]:
    allowed = set(batch_urls)
    failed: set[str] = set()
    values = response.get("failed_results")
    if not isinstance(values, list):
        return failed
    for item in values:
        if not isinstance(item, dict) or not isinstance(item.get("url"), str):
            continue
        try:
            url = _validate_public_http_url(item["url"])
        except ValueError:
            continue
        if url in allowed:
            failed.add(url)
    return failed


def _extract_excerpt(
    raw_content: str,
    query: str,
    max_chars: int,
) -> tuple[str, int, int]:
    anchor = 0
    exact_match = re.search(re.escape(query.strip()), raw_content, re.IGNORECASE)
    if exact_match is not None:
        anchor = exact_match.start()
    else:
        terms = sorted(
            {term for term in re.findall(r"[\w-]{3,}", query)},
            key=len,
            reverse=True,
        )
        for term in terms:
            match = re.search(re.escape(term), raw_content, re.IGNORECASE)
            if match is not None:
                anchor = match.start()
                break
    width = min(max_chars, len(raw_content))
    start = max(0, anchor - width // 3)
    end = min(len(raw_content), start + width)
    start = max(0, end - width)
    return raw_content[start:end], start, end


def _safe_request_id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    request_id = value.strip()
    if any(secret in request_id for secret in _environment_secret_values()):
        return None
    return request_id if _REQUEST_ID_PATTERN.fullmatch(request_id) else None


def _safe_extract_usage(value: Any) -> dict[str, int | float]:
    if not isinstance(value, dict):
        return {}
    credits = value.get("credits")
    if (
        isinstance(credits, int | float)
        and not isinstance(credits, bool)
        and credits >= 0
    ):
        return {"credits": credits}
    return {}


def _environment_secret_values() -> tuple[str, ...]:
    values = []
    for key, raw_value in os.environ.items():
        value = raw_value.strip()
        if len(value) < 4:
            continue
        if value.casefold() in _NON_SECRET_CONTROL_VALUES:
            continue
        if any(marker in key.casefold() for marker in _SECRET_ENV_MARKERS):
            values.append(value)
    return tuple(dict.fromkeys(values))


def _redact_sensitive_text(value: str) -> str:
    redacted = value
    for secret in sorted(_environment_secret_values(), key=len, reverse=True):
        redacted = redacted.replace(secret, "[REDACTED]")
    redacted = _AUTHORIZATION_PATTERN.sub(r"\1[REDACTED]", redacted)
    redacted = _BEARER_PATTERN.sub("[REDACTED]", redacted)
    return _SECRET_FIELD_PATTERN.sub(r"\1[REDACTED]", redacted)


def _safe_output_query(value: str) -> str:
    return " ".join(_redact_sensitive_text(value).split())[:1000]


def _safe_search_title(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(_redact_sensitive_text(value).split())[
        :TAVILY_SEARCH_TITLE_MAX_CHARS
    ]


def _safe_search_snippet(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return _redact_sensitive_text(value)[:TAVILY_SEARCH_SNIPPET_MAX_CHARS]


def _research_evidence_artifact(
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the only structured artifact persisted from Tavily tool output."""
    return {
        "type": RESEARCH_EVIDENCE_ARTIFACT_TYPE,
        "version": RESEARCH_EVIDENCE_ARTIFACT_VERSION,
        "records": records,
    }

async def tavily_search_async(
    search_queries, 
    max_results: int = 5, 
    topic: Literal["general", "news", "finance"] = "general", 
    include_raw_content: bool = True, 
    config: RunnableConfig = None
):
    """Execute multiple Tavily search queries asynchronously.
    
    Args:
        search_queries: List of search query strings to execute
        max_results: Maximum number of results per query
        topic: Topic category for filtering results
        include_raw_content: Whether to include full webpage content
        config: Runtime configuration for API key access
        
    Returns:
        List of search result dictionaries from Tavily API
    """
    # Initialize the Tavily client with API key from config
    tavily_client = AsyncTavilyClient(api_key=get_tavily_api_key(config))
    
    # Create search tasks for parallel execution
    search_tasks = [
        tavily_client.search(
            query,
            max_results=max_results,
            include_raw_content=include_raw_content,
            topic=topic
        )
        for query in search_queries
    ]
    
    # Execute all search queries in parallel and return results
    search_results = await asyncio.gather(*search_tasks)
    return search_results

async def summarize_webpage(model: BaseChatModel, webpage_content: str) -> str:
    """Summarize webpage content using AI model with timeout protection.
    
    Args:
        model: The chat model configured for summarization
        webpage_content: Raw webpage content to be summarized
        
    Returns:
        Formatted summary with key excerpts, or a stable bounded failure marker
    """
    try:
        # Create prompt with current date context
        prompt_content = summarize_webpage_prompt.format(date=get_today_str())
        untrusted_content = (
            "<untrusted_webpage_content>\n"
            f"{webpage_content}\n"
            "</untrusted_webpage_content>"
        )
        
        # The model already carries the configured SDK request timeout.
        summary = await model.ainvoke([
                SystemMessage(content=prompt_content),
                HumanMessage(content=untrusted_content),
            ])
        
        # Format the summary with structured sections
        formatted_summary = (
            f"<summary>\n{summary.summary}\n</summary>\n\n"
            f"<key_excerpts>\n{summary.key_excerpts}\n</key_excerpts>"
        )
        
        return formatted_summary
        
    except asyncio.TimeoutError:
        logging.warning(
            "Summarization reached the configured request timeout"
        )
        return TAVILY_SUMMARY_FAILURE_MARKER
    except Exception as e:
        logging.warning(
            "Summarization failed (%s)",
            type(e).__name__,
        )
        return TAVILY_SUMMARY_FAILURE_MARKER

##########################
# Reflection Tool Utils
##########################

@tool(description="Strategic reflection tool for research planning")
def think_tool(reflection: str) -> str:
    """Tool for strategic reflection on research progress and decision-making.

    Use this tool after each search to analyze results and plan next steps systematically.
    This creates a deliberate pause in the research workflow for quality decision-making.

    When to use:
    - After receiving search results: What key information did I find?
    - Before deciding next steps: Do I have enough to answer comprehensively?
    - When assessing research gaps: What specific information am I still missing?
    - Before concluding research: Can I provide a complete answer now?

    Reflection should address:
    1. Analysis of current findings - What concrete information have I gathered?
    2. Gap assessment - What crucial information is still missing?
    3. Quality evaluation - Do I have sufficient evidence/examples for a good answer?
    4. Strategic decision - Should I continue searching or provide my answer?

    Args:
        reflection: Your detailed reflection on research progress, findings, gaps, and next steps

    Returns:
        Confirmation that reflection was recorded for decision-making
    """
    return f"Reflection recorded: {reflection}"

##########################
# MCP Utils
##########################

async def get_mcp_access_token(
    supabase_token: str,
    base_mcp_url: str,
) -> Optional[Dict[str, Any]]:
    """Exchange Supabase token for MCP access token using OAuth token exchange.
    
    Args:
        supabase_token: Valid Supabase authentication token
        base_mcp_url: Base URL of the MCP server
        
    Returns:
        Token data dictionary if successful, None if failed
    """
    try:
        # Prepare OAuth token exchange request data
        form_data = {
            "client_id": "mcp_default",
            "subject_token": supabase_token,
            "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
            "resource": base_mcp_url.rstrip("/") + "/mcp",
            "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
        }
        
        # Execute token exchange request
        async with aiohttp.ClientSession() as session:
            token_url = base_mcp_url.rstrip("/") + "/oauth/token"
            headers = {"Content-Type": "application/x-www-form-urlencoded"}
            
            async with session.post(token_url, headers=headers, data=form_data) as response:
                if response.status == 200:
                    # Successfully obtained token
                    token_data = await response.json()
                    return token_data
                else:
                    # Log error details for debugging
                    response_text = await response.text()
                    logging.error(f"Token exchange failed: {response_text}")
                    
    except Exception as e:
        logging.error(f"Error during token exchange: {e}")
    
    return None

async def get_tokens(config: RunnableConfig):
    """Retrieve stored authentication tokens with expiration validation.
    
    Args:
        config: Runtime configuration containing thread and user identifiers
        
    Returns:
        Token dictionary if valid and not expired, None otherwise
    """
    store = get_store()
    
    # Extract required identifiers from config
    thread_id = config.get("configurable", {}).get("thread_id")
    if not thread_id:
        return None
        
    user_id = config.get("metadata", {}).get("owner")
    if not user_id:
        return None
    
    # Retrieve stored tokens
    tokens = await store.aget((user_id, "tokens"), "data")
    if not tokens:
        return None
    
    # Check token expiration
    expires_in = tokens.value.get("expires_in")  # seconds until expiration
    created_at = tokens.created_at  # datetime of token creation
    current_time = datetime.now(timezone.utc)
    expiration_time = created_at + timedelta(seconds=expires_in)
    
    if current_time > expiration_time:
        # Token expired, clean up and return None
        await store.adelete((user_id, "tokens"), "data")
        return None

    return tokens.value

async def set_tokens(config: RunnableConfig, tokens: dict[str, Any]):
    """Store authentication tokens in the configuration store.
    
    Args:
        config: Runtime configuration containing thread and user identifiers
        tokens: Token dictionary to store
    """
    store = get_store()
    
    # Extract required identifiers from config
    thread_id = config.get("configurable", {}).get("thread_id")
    if not thread_id:
        return
        
    user_id = config.get("metadata", {}).get("owner")
    if not user_id:
        return
    
    # Store the tokens
    await store.aput((user_id, "tokens"), "data", tokens)

async def fetch_tokens(config: RunnableConfig) -> dict[str, Any]:
    """Fetch and refresh MCP tokens, obtaining new ones if needed.
    
    Args:
        config: Runtime configuration with authentication details
        
    Returns:
        Valid token dictionary, or None if unable to obtain tokens
    """
    # Try to get existing valid tokens first
    current_tokens = await get_tokens(config)
    if current_tokens:
        return current_tokens
    
    # Extract Supabase token for new token exchange
    supabase_token = config.get("configurable", {}).get("x-supabase-access-token")
    if not supabase_token:
        return None
    
    # Extract MCP configuration
    mcp_config = config.get("configurable", {}).get("mcp_config")
    if not mcp_config or not mcp_config.get("url"):
        return None
    
    # Exchange Supabase token for MCP tokens
    mcp_tokens = await get_mcp_access_token(supabase_token, mcp_config.get("url"))
    if not mcp_tokens:
        return None

    # Store the new tokens and return them
    await set_tokens(config, mcp_tokens)
    return mcp_tokens

def wrap_mcp_authenticate_tool(tool: StructuredTool) -> StructuredTool:
    """Wrap MCP tool with comprehensive authentication and error handling.
    
    Args:
        tool: The MCP structured tool to wrap
        
    Returns:
        Enhanced tool with authentication error handling
    """
    original_coroutine = tool.coroutine
    
    async def authentication_wrapper(**kwargs):
        """Enhanced coroutine with MCP error handling and user-friendly messages."""
        
        def _find_mcp_error_in_exception_chain(exc: BaseException) -> McpError | None:
            """Recursively search for MCP errors in exception chains."""
            if isinstance(exc, McpError):
                return exc
            
            # Handle ExceptionGroup (Python 3.11+) by checking attributes
            if hasattr(exc, 'exceptions'):
                for sub_exception in exc.exceptions:
                    if found_error := _find_mcp_error_in_exception_chain(sub_exception):
                        return found_error
            return None
        
        try:
            # Execute the original tool functionality
            return await original_coroutine(**kwargs)
            
        except BaseException as original_error:
            # Search for MCP-specific errors in the exception chain
            mcp_error = _find_mcp_error_in_exception_chain(original_error)
            if not mcp_error:
                # Not an MCP error, re-raise the original exception
                raise original_error
            
            # Handle MCP-specific error cases
            error_details = mcp_error.error
            error_code = getattr(error_details, "code", None)
            error_data = getattr(error_details, "data", None) or {}
            
            # Check for authentication/interaction required error
            if error_code == -32003:  # Interaction required error code
                message_payload = error_data.get("message", {})
                error_message = "Required interaction"
                
                # Extract user-friendly message if available
                if isinstance(message_payload, dict):
                    error_message = message_payload.get("text") or error_message
                
                # Append URL if provided for user reference
                if url := error_data.get("url"):
                    error_message = f"{error_message} {url}"
                
                raise ToolException(error_message) from original_error
            
            # For other MCP errors, re-raise the original
            raise original_error
    
    # Replace the tool's coroutine with our enhanced version
    tool.coroutine = authentication_wrapper
    return tool

async def load_mcp_tools(
    config: RunnableConfig,
    existing_tool_names: set[str],
) -> list[BaseTool]:
    """Load and configure MCP (Model Context Protocol) tools with authentication.
    
    Args:
        config: Runtime configuration containing MCP server details
        existing_tool_names: Set of tool names already in use to avoid conflicts
        
    Returns:
        List of configured MCP tools ready for use
    """
    configurable = Configuration.from_runnable_config(config)
    
    # Step 1: Handle authentication if required
    if configurable.mcp_config and configurable.mcp_config.auth_required:
        mcp_tokens = await fetch_tokens(config)
    else:
        mcp_tokens = None
    
    # Step 2: Validate configuration requirements
    config_valid = (
        configurable.mcp_config and 
        configurable.mcp_config.url and 
        configurable.mcp_config.tools and 
        (mcp_tokens or not configurable.mcp_config.auth_required)
    )
    
    if not config_valid:
        return []
    
    # Step 3: Set up MCP server connection
    server_url = configurable.mcp_config.url.rstrip("/") + "/mcp"
    
    # Configure authentication headers if tokens are available
    auth_headers = None
    if mcp_tokens:
        auth_headers = {"Authorization": f"Bearer {mcp_tokens['access_token']}"}
    
    mcp_server_config = {
        "server_1": {
            "url": server_url,
            "headers": auth_headers,
            "transport": "streamable_http"
        }
    }
    # TODO: When Multi-MCP Server support is merged in OAP, update this code
    
    # Step 4: Load tools from MCP server
    try:
        client = MultiServerMCPClient(mcp_server_config)
        available_mcp_tools = await client.get_tools()
    except Exception:
        # If MCP server connection fails, return empty list
        return []
    
    # Step 5: Filter and configure tools
    configured_tools = []
    for mcp_tool in available_mcp_tools:
        # Skip tools with conflicting names
        if mcp_tool.name in existing_tool_names:
            warnings.warn(
                f"MCP tool '{mcp_tool.name}' conflicts with existing tool name - skipping"
            )
            continue
        
        # Only include tools specified in configuration
        if mcp_tool.name not in set(configurable.mcp_config.tools):
            continue
        
        # Wrap tool with authentication handling and add to list
        enhanced_tool = wrap_mcp_authenticate_tool(mcp_tool)
        configured_tools.append(enhanced_tool)
    
    return configured_tools


##########################
# Tool Utils
##########################

async def get_search_tool(search_api: SearchAPI):
    """Configure and return search tools based on the specified API provider.
    
    Args:
        search_api: The search API provider to use (Anthropic, OpenAI, Tavily, or None)
        
    Returns:
        List of configured search tool objects for the specified provider
    """
    if search_api == SearchAPI.ANTHROPIC:
        # Anthropic's native web search with usage limits
        return [{
            "type": "web_search_20250305", 
            "name": "web_search", 
            "max_uses": 5
        }]
        
    elif search_api == SearchAPI.OPENAI:
        # OpenAI's web search preview functionality
        return [{"type": "web_search_preview"}]
        
    elif search_api == SearchAPI.TAVILY:
        # Configure only the official Tavily search and extract tools.
        search_tool = tavily_search
        search_tool.metadata = {
            **(search_tool.metadata or {}), 
            "type": "search", 
            "name": "web_search"
        }
        extract_tool = tavily_extract
        extract_tool.metadata = {
            **(extract_tool.metadata or {}),
            "type": "extract",
            "name": "web_extract",
        }
        return [search_tool, extract_tool]
        
    elif search_api == SearchAPI.NONE:
        # No search functionality configured
        return []
        
    # Default fallback for unknown search API types
    return []
    
async def get_all_tools(config: RunnableConfig):
    """Assemble complete toolkit including research, search, and MCP tools.
    
    Args:
        config: Runtime configuration specifying search API and MCP settings
        
    Returns:
        List of all configured and available tools for research operations
    """
    # Start with core research tools
    tools = [tool(ResearchComplete), think_tool]
    
    # Add configured search tools
    configurable = Configuration.from_runnable_config(config)
    search_api = SearchAPI(get_config_value(configurable.search_api))
    search_tools = await get_search_tool(search_api)
    tools.extend(search_tools)
    
    # Track existing tool names to prevent conflicts
    existing_tool_names = {
        tool.name if hasattr(tool, "name") else tool.get("name", "web_search") 
        for tool in tools
    }
    
    # Add MCP tools if configured
    mcp_tools = await load_mcp_tools(config, existing_tool_names)
    tools.extend(mcp_tools)
    
    return tools

def get_notes_from_tool_calls(messages: list[MessageLikeRepresentation]):
    """Extract notes from tool call messages."""
    return [tool_msg.content for tool_msg in filter_messages(messages, include_types="tool")]

##########################
# Model Provider Native Websearch Utils
##########################

def anthropic_websearch_count(response) -> int:
    """Return the number of Anthropic server-side web search requests.
    
    Args:
        response: The response object from Anthropic's API
        
    Returns:
        The non-negative request count, or zero for unknown response shapes.
    """
    try:
        # Navigate through the response metadata structure
        usage = response.response_metadata.get("usage")
        if not usage:
            return 0
        
        # Check for server-side tool usage information
        server_tool_use = usage.get("server_tool_use")
        if not server_tool_use:
            return 0
        
        # Look for web search request count
        web_search_requests = server_tool_use.get("web_search_requests")
        if web_search_requests is None:
            return 0
        
        if isinstance(web_search_requests, bool):
            return int(web_search_requests)
        if isinstance(web_search_requests, int):
            return max(0, web_search_requests)
        return 0
        
    except (AttributeError, TypeError):
        # Handle cases where response structure is unexpected
        return 0


def anthropic_websearch_called(response):
    """Detect if Anthropic's native web search was used in the response."""
    return anthropic_websearch_count(response) > 0


def openai_websearch_count(response) -> int:
    """Return the number of OpenAI native web search call outputs.
    
    Args:
        response: The response object from OpenAI's API
        
    Returns:
        The non-negative call count, or zero for unknown response shapes.
    """
    # Check for tool outputs in the response metadata
    tool_outputs = response.additional_kwargs.get("tool_outputs")
    if not tool_outputs:
        return 0
    
    return sum(
        isinstance(tool_output, dict)
        and tool_output.get("type") == "web_search_call"
        for tool_output in tool_outputs
    )


def openai_websearch_called(response):
    """Detect if OpenAI's native web search was used in the response."""
    return openai_websearch_count(response) > 0


##########################
# Token Limit Exceeded Utils
##########################

def is_token_limit_exceeded(exception: Exception, model_name: str = None) -> bool:
    """Determine if an exception indicates a token/context limit was exceeded.
    
    Args:
        exception: The exception to analyze
        model_name: Optional model name to optimize provider detection
        
    Returns:
        True if the exception indicates a token limit was exceeded, False otherwise
    """
    error_str = str(exception).lower()
    
    # Step 1: Determine provider from model name if available
    provider = None
    if model_name:
        model_str = str(model_name).lower()
        if model_str.startswith('openai:'):
            provider = 'openai'
        elif model_str.startswith('anthropic:'):
            provider = 'anthropic'
        elif model_str.startswith('gemini:') or model_str.startswith('google:'):
            provider = 'gemini'
    
    # Step 2: Check provider-specific token limit patterns
    if provider == 'openai':
        return _check_openai_token_limit(exception, error_str)
    elif provider == 'anthropic':
        return _check_anthropic_token_limit(exception, error_str)
    elif provider == 'gemini':
        return _check_gemini_token_limit(exception, error_str)
    
    # Step 3: If provider unknown, check all providers
    return (
        _check_openai_token_limit(exception, error_str) or
        _check_anthropic_token_limit(exception, error_str) or
        _check_gemini_token_limit(exception, error_str)
    )

def _check_openai_token_limit(exception: Exception, error_str: str) -> bool:
    """Check if exception indicates OpenAI token limit exceeded."""
    # Analyze exception metadata
    exception_type = str(type(exception))
    class_name = exception.__class__.__name__
    module_name = getattr(exception.__class__, '__module__', '')
    
    # Check if this is an OpenAI exception
    is_openai_exception = (
        'openai' in exception_type.lower() or 
        'openai' in module_name.lower()
    )
    
    # Check for typical OpenAI token limit error types
    is_request_error = class_name in ['BadRequestError', 'InvalidRequestError']
    
    if is_openai_exception and is_request_error:
        # Look for token-related keywords in error message
        token_keywords = ['token', 'context', 'length', 'maximum context', 'reduce']
        if any(keyword in error_str for keyword in token_keywords):
            return True
    
    # Check for specific OpenAI error codes
    if hasattr(exception, 'code') and hasattr(exception, 'type'):
        error_code = getattr(exception, 'code', '')
        error_type = getattr(exception, 'type', '')
        
        if (error_code == 'context_length_exceeded' or
            error_type == 'invalid_request_error'):
            return True
    
    return False

def _check_anthropic_token_limit(exception: Exception, error_str: str) -> bool:
    """Check if exception indicates Anthropic token limit exceeded."""
    # Analyze exception metadata
    exception_type = str(type(exception))
    class_name = exception.__class__.__name__
    module_name = getattr(exception.__class__, '__module__', '')
    
    # Check if this is an Anthropic exception
    is_anthropic_exception = (
        'anthropic' in exception_type.lower() or 
        'anthropic' in module_name.lower()
    )
    
    # Check for Anthropic-specific error patterns
    is_bad_request = class_name == 'BadRequestError'
    
    if is_anthropic_exception and is_bad_request:
        # Anthropic uses specific error messages for token limits
        if 'prompt is too long' in error_str:
            return True
    
    return False

def _check_gemini_token_limit(exception: Exception, error_str: str) -> bool:
    """Check if exception indicates Google/Gemini token limit exceeded."""
    # Analyze exception metadata
    exception_type = str(type(exception))
    class_name = exception.__class__.__name__
    module_name = getattr(exception.__class__, '__module__', '')
    
    # Check if this is a Google/Gemini exception
    is_google_exception = (
        'google' in exception_type.lower() or 
        'google' in module_name.lower()
    )
    
    # Check for Google-specific resource exhaustion errors
    is_resource_exhausted = class_name in [
        'ResourceExhausted', 
        'GoogleGenerativeAIFetchError'
    ]
    
    if is_google_exception and is_resource_exhausted:
        return True
    
    # Check for specific Google API resource exhaustion patterns
    if 'google.api_core.exceptions.resourceexhausted' in exception_type.lower():
        return True
    
    return False

# NOTE: This may be out of date or not applicable to your models. Please update this as needed.
MODEL_TOKEN_LIMITS = {
    "openai:gpt-4.1-mini": 1047576,
    "openai:gpt-4.1-nano": 1047576,
    "openai:gpt-4.1": 1047576,
    "openai:gpt-4o-mini": 128000,
    "openai:gpt-4o": 128000,
    "openai:o4-mini": 200000,
    "openai:o3-mini": 200000,
    "openai:o3": 200000,
    "openai:o3-pro": 200000,
    "openai:o1": 200000,
    "openai:o1-pro": 200000,
    "anthropic:claude-opus-4": 200000,
    "anthropic:claude-sonnet-4": 200000,
    "anthropic:claude-3-7-sonnet": 200000,
    "anthropic:claude-3-5-sonnet": 200000,
    "anthropic:claude-3-5-haiku": 200000,
    "google:gemini-1.5-pro": 2097152,
    "google:gemini-1.5-flash": 1048576,
    "google:gemini-pro": 32768,
    "cohere:command-r-plus": 128000,
    "cohere:command-r": 128000,
    "cohere:command-light": 4096,
    "cohere:command": 4096,
    "mistral:mistral-large": 32768,
    "mistral:mistral-medium": 32768,
    "mistral:mistral-small": 32768,
    "mistral:mistral-7b-instruct": 32768,
    "ollama:codellama": 16384,
    "ollama:llama2:70b": 4096,
    "ollama:llama2:13b": 4096,
    "ollama:llama2": 4096,
    "ollama:mistral": 32768,
    "bedrock:us.amazon.nova-premier-v1:0": 1000000,
    "bedrock:us.amazon.nova-pro-v1:0": 300000,
    "bedrock:us.amazon.nova-lite-v1:0": 300000,
    "bedrock:us.amazon.nova-micro-v1:0": 128000,
    "bedrock:us.anthropic.claude-3-7-sonnet-20250219-v1:0": 200000,
    "bedrock:us.anthropic.claude-sonnet-4-20250514-v1:0": 200000,
    "bedrock:us.anthropic.claude-opus-4-20250514-v1:0": 200000,
    "anthropic.claude-opus-4-1-20250805-v1:0": 200000,
}

def get_model_token_limit(model_string):
    """Look up the token limit for a specific model.
    
    Args:
        model_string: The model identifier string to look up
        
    Returns:
        Token limit as integer if found, None if model not in lookup table
    """
    # Search through known model token limits
    for model_key, token_limit in MODEL_TOKEN_LIMITS.items():
        if model_key in model_string:
            return token_limit
    
    # Model not found in lookup table
    return None

def remove_up_to_last_ai_message(messages: list[MessageLikeRepresentation]) -> list[MessageLikeRepresentation]:
    """Truncate message history by removing up to the last AI message.
    
    This is useful for handling token limit exceeded errors by removing recent context.
    
    Args:
        messages: List of message objects to truncate
        
    Returns:
        Truncated message list up to (but not including) the last AI message
    """
    # Search backwards through messages to find the last AI message
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], AIMessage):
            # Return everything up to (but not including) the last AI message
            return messages[:i]
    
    # No AI messages found, return original list
    return messages

##########################
# Misc Utils
##########################

def get_today_str() -> str:
    """Get current date formatted for display in prompts and outputs.
    
    Returns:
        Human-readable date string in format like 'Mon Jan 15, 2024'
    """
    now = datetime.now()
    return f"{now:%a} {now:%b} {now.day}, {now:%Y}"

def get_config_value(value):
    """Extract value from configuration, handling enums and None values."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    elif isinstance(value, dict):
        return value
    else:
        return value.value


@dataclass(frozen=True)
class RuntimeModelConnection:
    """Process-local connection values selected for one model route."""

    api_key: str | None = None
    base_url: str | None = None


_RUNTIME_MODEL_CONNECTIONS: ContextVar[
    Mapping[str, RuntimeModelConnection] | None
] = ContextVar("runtime_model_connections", default=None)


@contextmanager
def model_connection_context(
    connections: Mapping[str, RuntimeModelConnection],
):
    """Bind non-persistent connection values for one graph execution window."""
    token = _RUNTIME_MODEL_CONNECTIONS.set(dict(connections))
    try:
        yield
    finally:
        _RUNTIME_MODEL_CONNECTIONS.reset(token)


def _runtime_connection(role: str | None) -> RuntimeModelConnection | None:
    """Resolve a route-local connection without reading Graph configuration."""
    if role is None:
        return None
    connections = _RUNTIME_MODEL_CONNECTIONS.get()
    return None if connections is None else connections.get(role)


def get_api_key_for_model(
    model_name: str,
    config: RunnableConfig,
    *,
    connection_role: str | None = None,
):
    """Get an API key from process-local routing or the legacy environment."""
    connection = _runtime_connection(connection_role)
    if connection is not None and connection.api_key:
        return connection.api_key
    should_get_from_config = os.getenv("GET_API_KEYS_FROM_CONFIG", "false")
    model_name = model_name.lower()
    if should_get_from_config.lower() == "true":
        api_keys = config.get("configurable", {}).get("apiKeys", {})
        if not api_keys:
            return None
        if model_name.startswith("openai:"):
            return api_keys.get("OPENAI_API_KEY")
        elif model_name.startswith("anthropic:"):
            return api_keys.get("ANTHROPIC_API_KEY")
        elif model_name.startswith("google"):
            return api_keys.get("GOOGLE_API_KEY")
        return None
    else:
        if model_name.startswith("openai:"):
            return os.getenv("OPENAI_API_KEY")
        elif model_name.startswith("anthropic:"):
            return os.getenv("ANTHROPIC_API_KEY")
        elif model_name.startswith("google"):
            return os.getenv("GOOGLE_API_KEY")
        return None


def get_base_url_for_model(
    model_name: str,
    _config: RunnableConfig,
    *,
    connection_role: str | None = None,
) -> str | None:
    """Get a route-local Base URL without storing it in Graph state."""
    connection = _runtime_connection(connection_role)
    if connection is not None and connection.base_url:
        return connection.base_url
    normalized = model_name.lower()
    if normalized.startswith("openai:"):
        return os.getenv("OPENAI_BASE_URL")
    if normalized.startswith("anthropic:"):
        return os.getenv("ANTHROPIC_BASE_URL")
    return None

def get_tavily_api_key(config: RunnableConfig):
    """Get Tavily API key from environment or config."""
    should_get_from_config = os.getenv("GET_API_KEYS_FROM_CONFIG", "false")
    if should_get_from_config.lower() == "true":
        api_keys = config.get("configurable", {}).get("apiKeys", {})
        if not api_keys:
            return None
        return api_keys.get("TAVILY_API_KEY")
    else:
        return os.getenv("TAVILY_API_KEY")
