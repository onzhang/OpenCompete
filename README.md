# OpenCompete

OpenCompete 是基于 LangChain 官方 Open Deep Research 研究内核扩展的本地通用竞品分析系统。正式入口是本地 CLI：它在当前 Python 进程内构建并运行 LangGraph，使用 SQLite 恢复执行，并把结构化产物和报告写入本地目录。**运行 OpenCompete 不需要启动 Agent Server，也不依赖 LangGraph Studio、LangSmith、MCP Token Store 或远程部署平台。**

> 当前项目处于首版开发阶段。真实研究会访问模型和 Tavily，可能产生费用；建议先运行离线测试，再按供应商上下文、超时和并发能力配置研究规模与硬预算。本文后半部分完整保留了上游 Open Deep Research 说明，其中的 Agent Server、Studio、MCP 和部署入口仅用于上游兼容调试，不是 OpenCompete 的正式运行方式。

## 本地 CLI 快速开始

环境要求：Python 3.11 或更高版本、[`uv`](https://docs.astral.sh/uv/)。在本仓库根目录执行：

```bash
uv sync --extra dev
cp .env.example .env
cp opencompete.example.toml opencompete.toml
```

如果使用默认模型组合，在本地 `.env` 中填写：

```dotenv
OPENAI_API_KEY=你的_OpenAI_Key
TAVILY_API_KEY=你的_Tavily_Key
```

`.env` 已被 Git 忽略。API Key 只能放在环境变量或本地 `.env` 中，不要写进命令参数、`opencompete.toml`、Prompt 或任务输入。使用 Anthropic、Google 等其他模型时，再填写对应供应商密钥并修改 `opencompete.toml` 中的模型标识。

先检查 CLI，再创建任务：

```bash
uv run opencompete --help
uv run opencompete new "比较 Notion 与 Confluence 的产品能力和公司定位" \
  --entity Notion \
  --entity Confluence \
  --level product_service \
  --level company_organization
```

需要系统发现更多候选竞品时增加 `--discover`。系统会先展示候选名称、来源标题、URL 和摘要；用户选择添加、拒绝并可修正对象名称后，才会进入第二次完整范围确认：

```bash
uv run opencompete new "比较 Notion 与 Confluence 的产品能力" \
  --entity Notion --entity Confluence --discover
```

`new` 会先显示建议的对象、层级和维度，并给出 `run_id`。只有输入 `y` 或“是”确认后才会开始研究；输入 `n` 会取消任务。确认点或研究过程中退出进程后，可使用相同 `run_id` 恢复：

```bash
uv run opencompete resume <run_id>
```

支持的层级值是 `product_service`（产品与服务）、`company_organization`（公司与组织）和 `industry_market`（产业与市场）。不传 `--level` 时默认使用全部三个层级；`--entity`、`--exclude` 和 `--url` 均可重复传入。

## 本地 Web 控制台

除 CLI 外，仓库提供一个本地 Web 界面（FastAPI 后端 + Vue 3 前端），覆盖新建研究、候选与范围确认、研究过程实时直播（SSE 读取 `events.jsonl`）、报告阅读（引用可点、证据链、对比矩阵）与历史任务查询。

环境要求：除 Python 3.11+ 和 `uv` 外，前端还需要 Node.js 20.19 或更高版本与 npm。首次使用先完成一次性准备（后两步在已有 `.env` 和 `opencompete.toml` 时可跳过）：

```bash
uv sync --extra dev                              # 安装后端依赖
cp .env.example .env                             # 填写模型与 Tavily 密钥
cp opencompete.example.toml opencompete.toml     # 按需调整模型、并发和预算
cd frontend && npm install                       # 安装前端依赖
```

启动需要两个终端，均在仓库根目录执行：

```bash
# 终端 1：API 服务（默认 127.0.0.1:8123）
uv run opencompete-api

# 终端 2：前端开发服务器（默认 http://localhost:5178）
cd frontend
npm run dev
```

打开 `http://localhost:5178` 即可使用。前端经 Vite 代理把 `/api` 转发到 8123 端口；`opencompete-api` 支持 `--config`、`--host`、`--port` 参数。可先确认服务就绪：

```bash
curl http://127.0.0.1:8123/api/health    # 返回 HTTP 200
```

两个终端分别按 `Ctrl+C` 停止。

使用约束：

- **API 服务固定单进程单 worker**，与 CLI 共享同一 `.opencompete-data/`。请勿用多 worker 方式部署，也不要在研究执行期间同时用 CLI 对同一任务操作。
- **研究进行中请勿停止 API 服务**：完整研究在服务进程内后台执行，进程退出会把任务留在无法自动恢复的中间态（界面会标注「可能已中断」，可用 `uv run opencompete resume <run_id>` 从 CLI 恢复）。
- 查看历史任务与报告不需要任何 API Key；发起新研究需要 `.env` 中的模型与 Tavily 密钥，且外部调用只在范围确认冻结后才开始。
- Web 层的补充索引写在 `.opencompete-data/api-meta/`（仅缓存研究问题与对象名单等展示字段）；任务状态、预算与产物一律以 `runs/<run_id>/manifest.json` 为准。

## 命令一览

| 命令 | 作用 |
|------|------|
| `uv run opencompete new [query]` | 创建任务，进入范围确认，并在确认后研究 |
| `uv run opencompete resume <run_id>` | 从同一 SQLite Checkpoint 恢复确认点或未完成任务 |
| `uv run opencompete list` | 只读取本地产物并列出历史任务，不启动研究 |
| `uv run opencompete show <run_id>` | 读取并展示已发布且通过校验的完整或部分报告，不启动研究 |
| `uv run opencompete evaluate <dataset> --case <case_id> --run <run_id>` | 将一个固定案例绑定到已有报告并离线评测，不重新搜索 |
| `uv run opencompete evaluate <dataset> --targets <targets.json>` | 批量评测案例与历史任务的固定绑定 |
| `uv run opencompete-api` | 启动本地 Web 控制台的 API 服务（单进程，端口 8123） |

`--config` 和 `--data-dir` 是主命令选项，必须放在子命令前。例如：

```bash
uv run opencompete --config opencompete.toml --data-dir D:/opencompete-data list
```

批量评测绑定文件的最小格式如下：

```json
{
  "targets": [
    {
      "case_id": "ocb-001-aws-lambda-vs-azure-functions",
      "run_id": "已有的-run_id"
    }
  ]
}
```

## OpenCompete Bench 当前限制

仓库内置数据集位于 `benchmarks/opencompete/v1/`，包含 8 个固定案例。**这些案例的参考报告目前全部是 `draft`，尚未记录真实人工审核，因此 CLI 会默认拒绝把它们用于正式评测。** 这是一项防止把草稿冒充权威参考答案的安全门禁；当前 CLI 不提供绕过参数。完成人工审核并在案例元数据中记录审核人和时间后，正式命令示例如下：

```bash
uv run opencompete evaluate benchmarks/opencompete/v1 \
  --case ocb-001-aws-lambda-vs-azure-functions \
  --run <run_id>
```

评测只读取固定案例和已有本地产物，不调用 Tavily，也不修改被评报告。当前默认 Application Runner 没有配置报告 Judge，因此数据集通过审核后会保存硬门禁和成本结果，同时把 RACE、FACT 明确记为 `not_run`；不会生成没有依据的分数。

参考报告完成人工审核后，如需实际运行 RACE/FACT，可在 `opencompete.toml` 显式配置 LangChain 模型：

```toml
judge_model = "openai:gpt-4.1"
judge_model_max_tokens = 8192
judge_prompt_version = "opencompete-race-fact-v1"
```

Judge 默认关闭，只有设置 `judge_model` 后才会初始化模型并产生评测调用费用。生产 Judge 使用固定 Prompt、Reference Report、任务级 RACE/FACT Criteria 和历史 Bundle，不绑定工具或搜索；评测结果同时保存评测契约版本、案例内容哈希、Judge 模型和 Prompt 版本。该配置不会绕过 draft 人工审核门禁。

## 官方 Deep Research Bench RACE

`opencompete-race` 是独立的通用研究评估入口，不使用竞品分析 Scope，也不依赖 Agent Server。它固定官方 Deep Research Bench 提交 `469cce54ea7f6a63c163d3d9fec879cf289ec484`，只负责生成官方三字段 JSONL、调用官方 evaluator 和汇总量化结果；官方 Criteria、Reference 和评分源码保留在外部官方仓库。

```bash
git clone https://github.com/Ayanami0730/deep_research_bench.git \
  .opencompete-tools/deep_research_bench
git -C .opencompete-tools/deep_research_bench checkout --detach \
  469cce54ea7f6a63c163d3d9fec879cf289ec484

uv run opencompete-race verify .opencompete-tools/deep_research_bench
```

先运行少量任务；每个成功任务立即追加到 Article JSONL，重复执行会跳过已有 ID：

```bash
uv run opencompete-race generate .opencompete-tools/deep_research_bench \
  --articles .opencompete-data/drb/opencompete.jsonl \
  --metrics .opencompete-data/drb/generation-metrics.jsonl \
  --id 1 --id 51
```

配置官方 evaluator 所需的 `LLM_BACKEND`、对应 API Key 和 `RACE_MODEL` 后运行评分：

```bash
uv run opencompete-race score .opencompete-tools/deep_research_bench \
  --articles .opencompete-data/drb/opencompete.jsonl \
  --metrics .opencompete-data/drb/generation-metrics.jsonl \
  --work-dir .opencompete-data/drb/official-race \
  --target-name opencompete \
  --judge-model "$RACE_MODEL" \
  --summary .opencompete-data/drb/summary.json \
  --id 1 --id 51 \
  --max-workers 1
```

少量中英文混合任务应使用相同的 `--id` 传给生成和评分；临时 Query 子集仍来自已验证的官方 100 题。量化摘要分别保存报告完成率、评分完成率、四个 RACE 维度、RACE Overall 和平均运行成本。Judge 模型与官方指定的 GPT-5.5 一致时结果标记为 `official_race`；使用其他 Judge 时标记为 `race_compatible_local`。OpenCompete 本地 FACT、硬门禁和成本不会与 RACE 合成为自定义总分。

## 配置与密钥

- `opencompete.example.toml` 是不含密钥的配置模板；复制为 `opencompete.toml` 后可调整模型、并发和预算。
- `llm_request_timeout_seconds` 控制一次模型 SDK 请求的最长等待；`llm_sdk_max_retries = 0` 关闭 SDK 内部不可观测重试。
- `long_form_streaming = true` 只让压缩和最终报告使用 LangChain 官方流式聚合，避免兼容网关在长时间非流响应期间关闭连接；工具调用和结构化输出保持非流。
- `final_report_model_max_tokens` 与 `artifact_extraction_model_max_tokens` 分别控制可读报告和结构化提取，不能再共用一个输出上限。
- `max_concurrent_summarizations` 限制单个搜索结果批次中的网页摘要并发；摘要使用统一 SDK timeout，不再另设 60 秒硬超时。
- 可选模型降级封装在单次 LangChain Runnable 调用边界，默认最多五次真实尝试：主供应商三次、备用供应商两次。只有限流、超时、连接错误和 HTTP 5xx 会继续重试或切换；认证、权限和参数错误立即停止。Application Runner 不重试整张 Graph，因此并行成功调用不会因兄弟失败而重放。
- 可在 TOML 设置 `fallback_model = "anthropic:<model>"`，也可在 `.env` 设置 `ANTHROPIC_MODEL`、`ANTHROPIC_API_KEY` 和 `ANTHROPIC_BASE_URL`。DeepSeek Thinking 模型作为备用时默认关闭 Thinking，以兼容 LangChain 的结构化输出和强制工具选择。
- 主模型为 DeepSeek Thinking 系列（如经 Anthropic 兼容端点直连 `anthropic:deepseek-v4-flash`）时，需在 TOML 设置 `primary_disable_thinking = true`：研究、范围规划、候选发现、产物提取和 Judge 的全部主模型调用会统一注入 `thinking: disabled`。注意此时不能再配置 OpenAI 系备用模型——thinking 参数会作为未知参数发给 OpenAI 兼容端点并直接失败；临时错误只会在主供应商内重试。
- 重试期间 CLI 会显示当前阶段、“正在重新连接 n/5”或“正在切换备用模型 n/5”，并展示脱敏后的具体异常摘要。最终错误会继续显示异常类型、底层原因类型、枚举化传输原因、HTTP 状态、供应商错误码和请求 ID；原始异常链正文、响应正文、Header、请求正文、Key 与 Base URL 不会显示或持久化。
- `tavily_max_queries_per_call` 与 `tavily_max_results_per_query` 只约束上游 Researcher 的单次 Tavily 工具调用。Web 候选发现单独使用 `discovery_search_results_per_query`：默认每次获取最相关的 5 个公开来源，以避免大量无关长网页同时进入绑定模型；它不是候选产品数、查询数或来源总数上限。发现会按模型生成的独立检索词逐条搜索、聚合和去重；每轮来源绑定后，覆盖审计可为尚未覆盖的竞争空间追加去重检索词。只有覆盖饱和、预算耗尽或检索前沿耗尽才停止。`discovery_binding_max_input_chars` 只把过大的来源正文分批送入绑定模型，所有批次都会继续参与候选聚合和去重，并复用 `max_concurrent_summarizations` 的并发上限；`max_content_length`、`tavily_extract_excerpt_max_chars` 和 `final_report_input_max_chars` 控制进入兼容模型与业务 Evidence 的内容规模。
- Tavily Search 每条 query 和 Extract 返回的官方 credit 共用 `max_tavily_calls` 兼容预算字段；`llm_*_cost_per_million_usd`、`tavily_cost_per_call_usd` 和 `price_config_version` 只用于本地保守预留与复算，不代表供应商最终账单。
- 配置优先级是命令行非秘密覆盖、`OPENCOMPETE_` 环境变量、本地 `.env`、TOML、默认值。当前公开 CLI 覆盖项是 `--data-dir`。
- 默认模型标识为 `openai:gpt-4.1` 和 `openai:gpt-4.1-mini`，所以默认需要 `OPENAI_API_KEY`；正式搜索固定使用 Tavily，所以需要 `TAVILY_API_KEY`。
- LangSmith 遥测默认关闭。开启它也只能作为可选观测能力，不能成为创建、恢复、查看或评测任务的依赖。
- 不要把密钥写进 `OPENCOMPETE_*` 配置字段。Graph State、RunnableConfig、SQLite、日志和业务产物都不应包含 API Key。

## SQLite 与本地产物

默认数据目录是 `.opencompete-data/`，可通过 `--data-dir` 或 `data_dir` 修改：

```text
.opencompete-data/
├── checkpoints.sqlite3
└── runs/<run_id>/
    ├── manifest.json
    ├── bundle.json
    ├── report.md
    ├── events.jsonl
    └── evaluations/
```

- `checkpoints.sqlite3` 保存 LangGraph 可恢复执行状态，以及持久化的预算预留和外部调用记录。
- `manifest.json` 保存任务状态、产物版本、预算摘要和文件哈希；`bundle.json` 保存 `Source -> Evidence -> Claim -> Comparison -> Report` 结构化关系。
- `report.md` 是模型成功生成的完整用户报告；最终报告失败时才保存不含内部 ID 的部分保底报告。`bundle.json` 独立承担 FACT、硬门禁和证据追溯，不能用其审计表示替换用户正文。`events.jsonl` 是脱敏诊断事件，`evaluations/` 保存每次独立评测结果。
- 运行中以 SQLite Checkpoint 为恢复依据；报告发布后，`list` 和 `show` 从 `runs/<run_id>/` 读取长期业务产物。
- `.opencompete-data/` 已被 Git 忽略。不要手工同时从两个进程恢复同一个 `run_id`；首版只保证同一进程内互斥。

## 开发验证

以下检查均不需要 Agent Server、`langgraph.json`、网络或 API Key：

```bash
uv run pytest -q
uv run ruff check .
uv run mypy src/opencompete
```

真实供应商闭环是显式 `live` 测试，默认测试不会运行。先在被 Git 忽略的 `.env` 中配置模型、Base URL 和 Tavily，再单独执行：

```bash
uv run pytest -q -m live \
  tests/opencompete/test_live_full_report.py::test_live_full_report_end_to_end
```

该测试会真实执行搜索、研究、压缩、最终报告、结构化提取、SQLite 重载和跨进程 CLI `show`，并扫描 SQLite/WAL/本地产物中的密钥与 Authorization 模式。

---

## 上游 Open Deep Research 兼容说明（原文保留）

以下内容来自固定上游基线，用于理解和调试原始 Open Deep Research。其 `langgraph dev`、LangGraph Studio、LangSmith、MCP、Hosted deployment 和 Open Agent Platform 路径不属于 OpenCompete 本地 CLI 的正式依赖。

# 🔬 Open Deep Research

<img width="1388" height="298" alt="full_diagram" src="https://github.com/user-attachments/assets/12a2371b-8be2-4219-9b48-90503eb43c69" />

Deep research has broken out as one of the most popular agent applications. This is a simple, configurable, fully open source deep research agent that works across many model providers, search tools, and MCP servers. It's performance is on par with many popular deep research agents ([see Deep Research Bench leaderboard](https://huggingface.co/spaces/Ayanami0730/DeepResearch-Leaderboard)).

<img width="817" height="666" alt="Screenshot 2025-07-13 at 11 21 12 PM" src="https://github.com/user-attachments/assets/052f2ed3-c664-4a4f-8ec2-074349dcaa3f" />

### 🔥 Recent Updates

**August 14, 2025**: See our free course [here](https://academy.langchain.com/courses/deep-research-with-langgraph) (and course repo [here](https://github.com/langchain-ai/deep_research_from_scratch)) on building open deep research.

**August 7, 2025**: Added GPT-5 and updated the Deep Research Bench evaluation w/ GPT-5 results.

**August 2, 2025**: Achieved #6 ranking on the [Deep Research Bench Leaderboard](https://huggingface.co/spaces/Ayanami0730/DeepResearch-Leaderboard) with an overall score of 0.4344. 

**July 30, 2025**: Read about the evolution from our original implementations to the current version in our [blog post](https://rlancemartin.github.io/2025/07/30/bitter_lesson/).

**July 16, 2025**: Read more in our [blog](https://blog.langchain.com/open-deep-research/) and watch our [video](https://www.youtube.com/watch?v=agGiWUpxkhg) for a quick overview.

### 🚀 Quickstart

1. Clone the repository and activate a virtual environment:
```bash
git clone https://github.com/langchain-ai/open_deep_research.git
cd open_deep_research
uv venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

2. Install dependencies:
```bash
uv sync
# or
uv pip install -r pyproject.toml
```

3. Set up your `.env` file to customize the environment variables (for model selection, search tools, and other configuration settings):
```bash
cp .env.example .env
```

4. Launch agent with the LangGraph server locally:

```bash
# Install dependencies and start the LangGraph server
uvx --refresh --from "langgraph-cli[inmem]" --with-editable . --python 3.11 langgraph dev --allow-blocking
```

This will open the LangGraph Studio UI in your browser.

```
- 🚀 API: http://127.0.0.1:2024
- 🎨 Studio UI: https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024
- 📚 API Docs: http://127.0.0.1:2024/docs
```

Ask a question in the `messages` input field and click `Submit`. Select different configuration in the "Manage Assistants" tab.

### ⚙️ Configurations

#### LLM :brain:

Open Deep Research supports a wide range of LLM providers via the [init_chat_model() API](https://python.langchain.com/docs/how_to/chat_models_universal_init/). It uses LLMs for a few different tasks. See the below model fields in the [configuration.py](https://github.com/langchain-ai/open_deep_research/blob/main/src/open_deep_research/configuration.py) file for more details. This can be accessed via the LangGraph Studio UI. 

- **Summarization** (default: `openai:gpt-4.1-mini`): Summarizes search API results
- **Research** (default: `openai:gpt-4.1`): Power the search agent
- **Compression** (default: `openai:gpt-4.1`): Compresses research findings
- **Final Report Model** (default: `openai:gpt-4.1`): Write the final report

> Note: the selected model will need to support [structured outputs](https://python.langchain.com/docs/integrations/chat/) and [tool calling](https://python.langchain.com/docs/how_to/tool_calling/).

> Note: For OpenRouter: Follow [this guide](https://github.com/langchain-ai/open_deep_research/issues/75#issuecomment-2811472408) and for local models via Ollama  see [setup instructions](https://github.com/langchain-ai/open_deep_research/issues/65#issuecomment-2743586318).

#### Search API :mag:

Open Deep Research supports a wide range of search tools. By default it uses the [Tavily](https://www.tavily.com/) search API. Has full MCP compatibility and work native web search for Anthropic and OpenAI. See the `search_api` and `mcp_config` fields in the [configuration.py](https://github.com/langchain-ai/open_deep_research/blob/main/src/open_deep_research/configuration.py) file for more details. This can be accessed via the LangGraph Studio UI. 

#### Other 

See the fields in the [configuration.py](https://github.com/langchain-ai/open_deep_research/blob/main/src/open_deep_research/configuration.py) for various other settings to customize the behavior of Open Deep Research. 

### 📊 Evaluation

Open Deep Research is configured for evaluation with [Deep Research Bench](https://huggingface.co/spaces/Ayanami0730/DeepResearch-Leaderboard). This benchmark has 100 PhD-level research tasks (50 English, 50 Chinese), crafted by domain experts across 22 fields (e.g., Science & Tech, Business & Finance) to mirror real-world deep-research needs. It has 2 evaluation metrics, but the leaderboard is based on the RACE score. This uses LLM-as-a-judge (Gemini) to evaluate research reports against a golden set of reports compiled by experts across a set of metrics.

#### Usage

> Warning: Running across the 100 examples can cost ~$20-$100 depending on the model selection.

The dataset is available on [LangSmith via this link](https://smith.langchain.com/public/c5e7a6ad-fdba-478c-88e6-3a388459ce8b/d). To kick off evaluation, run the following command:

```bash
# Run comprehensive evaluation on LangSmith datasets
python tests/run_evaluate.py
```

This will provide a link to a LangSmith experiment, which will have a name `YOUR_EXPERIMENT_NAME`. Once this is done, extract the results to a JSONL file that can be submitted to the Deep Research Bench.

```bash
python tests/extract_langsmith_data.py --project-name "YOUR_EXPERIMENT_NAME" --model-name "you-model-name" --dataset-name "deep_research_bench"
```

This creates `tests/expt_results/deep_research_bench_model-name.jsonl` with the required format. Move the generated JSONL file to a local clone of the Deep Research Bench repository and follow their [Quick Start guide](https://github.com/Ayanami0730/deep_research_bench?tab=readme-ov-file#quick-start) for evaluation submission.

#### Results 

| Name | Commit | Summarization | Research | Compression | Total Cost | Total Tokens | RACE Score | Experiment |
|------|--------|---------------|----------|-------------|------------|--------------|------------|------------|
| GPT-5 | [ca3951d](https://github.com/langchain-ai/open_deep_research/pull/168/commits) | openai:gpt-4.1-mini | openai:gpt-5 | openai:gpt-4.1 |  | 204,640,896 | 0.4943 | [Link](https://smith.langchain.com/o/ebbaf2eb-769b-4505-aca2-d11de10372a4/datasets/6e4766ca-613c-4bda-8bde-f64f0422bbf3/compare?selectedSessions=4d5941c8-69ce-4f3d-8b3e-e3c99dfbd4cc&baseline=undefined) |
| Defaults | [6532a41](https://github.com/langchain-ai/open_deep_research/commit/6532a4176a93cc9bb2102b3d825dcefa560c85d9) | openai:gpt-4.1-mini | openai:gpt-4.1 | openai:gpt-4.1 | $45.98 | 58,015,332 | 0.4309 | [Link](https://smith.langchain.com/o/ebbaf2eb-769b-4505-aca2-d11de10372a4/datasets/6e4766ca-6[…]ons=cf4355d7-6347-47e2-a774-484f290e79bc&baseline=undefined) |
| Claude Sonnet 4 | [f877ea9](https://github.com/langchain-ai/open_deep_research/pull/163/commits/f877ea93641680879c420ea991e998b47aab9bcc) | openai:gpt-4.1-mini | anthropic:claude-sonnet-4-20250514 | openai:gpt-4.1 | $187.09 | 138,917,050 | 0.4401 | [Link](https://smith.langchain.com/o/ebbaf2eb-769b-4505-aca2-d11de10372a4/datasets/6e4766ca-6[…]ons=04f6002d-6080-4759-bcf5-9a52e57449ea&baseline=undefined) |
| Deep Research Bench Submission | [c0a160b](https://github.com/langchain-ai/open_deep_research/commit/c0a160b57a9b5ecd4b8217c3811a14d8eff97f72) | openai:gpt-4.1-nano | openai:gpt-4.1 | openai:gpt-4.1 | $87.83 | 207,005,549 | 0.4344 | [Link](https://smith.langchain.com/o/ebbaf2eb-769b-4505-aca2-d11de10372a4/datasets/6e4766ca-6[…]ons=e6647f74-ad2f-4cb9-887e-acb38b5f73c0&baseline=undefined) |

### 🚀 Deployments and Usage

#### LangGraph Studio

Follow the [quickstart](#-quickstart) to start LangGraph server locally and test the agent out on LangGraph Studio.

#### Hosted deployment
 
You can easily deploy to [LangGraph Platform](https://langchain-ai.github.io/langgraph/concepts/#deployment-options). 

#### Open Agent Platform

Open Agent Platform (OAP) is a UI from which non-technical users can build and configure their own agents. OAP is great for allowing users to configure the Deep Researcher with different MCP tools and search APIs that are best suited to their needs and the problems that they want to solve.

We've deployed Open Deep Research to our public demo instance of OAP. All you need to do is add your API Keys, and you can test out the Deep Researcher for yourself! Try it out [here](https://oap.langchain.com)

You can also deploy your own instance of OAP, and make your own custom agents (like Deep Researcher) available on it to your users.
1. [Deploy Open Agent Platform](https://docs.oap.langchain.com/quickstart)
2. [Add Deep Researcher to OAP](https://docs.oap.langchain.com/setup/agents)

### Legacy Implementations 🏛️

The `src/legacy/` folder contains two earlier implementations that provide alternative approaches to automated research. They are less performant than the current implementation, but provide alternative ideas understanding the different approaches to deep research.

#### 1. Workflow Implementation (`legacy/graph.py`)
- **Plan-and-Execute**: Structured workflow with human-in-the-loop planning
- **Sequential Processing**: Creates sections one by one with reflection
- **Interactive Control**: Allows feedback and approval of report plans
- **Quality Focused**: Emphasizes accuracy through iterative refinement

#### 2. Multi-Agent Implementation (`legacy/multi_agent.py`)  
- **Supervisor-Researcher Architecture**: Coordinated multi-agent system
- **Parallel Processing**: Multiple researchers work simultaneously
- **Speed Optimized**: Faster report generation through concurrency
- **MCP Support**: Extensive Model Context Protocol integration
