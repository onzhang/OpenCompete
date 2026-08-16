# OpenCompete 完整真实测试报告

> 更正：本报告记录的任务完成了模型与搜索链路，但当时发布的 `report.md` 被确定性审计 Renderer 替换，不能作为用户报告质量通过的证据。该问题已在 `2026-07-16-报告发布边界修复真实测试报告.md` 中重新测试；本文件保留为故障历史。

## 1. 测试结论

2026-07-16 使用用户提供的主供应商、DeepSeek 备用供应商和 Tavily 完成真实环境修复与复测。最终从头任务通过自动化 live 验收，完整覆盖候选发现、两次用户确认、研究、流式压缩、流式最终报告、结构化提取、本地持久化、独立 CLI 查看和密钥落盘扫描。

最终任务：`226dd316-0959-4156-b2b0-085df48869f0`。

最终状态为 `partial`，不是链路失败：第三个发现候选没有形成可验证结论，系统按契约补充 `Unknown` 和比较缺口，没有把证据不足伪装成确定事实。GitHub Copilot 与 Cursor 均形成结构化结论和比较。

## 2. 用户流程

1. 用户输入 GitHub Copilot 与 Cursor 的产品能力、价格研究目标。
2. 系统使用 Tavily 发现额外候选，并展示候选网页标题、来源 URL 和摘要。
3. 用户选择一个候选加入，未选候选视为拒绝；用户可修正候选对象名称。
4. 系统生成包含三个对象、产品服务层和指定维度的最终范围。
5. 用户第二次确认完整范围，系统冻结 Scope 后才开始研究。
6. 系统执行 Supervisor、Researcher、Tavily、网页摘要和证据整理。
7. 压缩和最终报告使用流式传输；结构化提取保持非流。
8. 系统生成 Bundle 与报告，使用新 Runner 重新加载，并由独立 CLI `show` 查看。
9. 系统扫描 SQLite 和全部运行产物，确认当前模型 Key、Tavily Key和 Authorization 模式未落盘。

## 3. 故障复现与根因

修复前，本地最终报告请求约 61 秒后出现 `APIConnectionError`，异常链为 `RemoteProtocolError: incomplete chunked read`；供应商后台同一请求继续运行 2 分 55 秒并完成生成。请求开始时间相差不足一秒，证明请求已到达供应商，失败发生在非流完整响应返回链路。

当前锁定 LangChain 版本支持官方流式聚合。修复仅对 `compress_research` 和 `final_report_generation` 设置 `streaming=True`；工具调用、结构化输出和网页摘要没有全局开启流式。

## 4. 修复内容

- 长文本压缩和最终报告使用 LangChain 官方流式聚合。
- 最终报告输出上限调整为 6000；Artifact Extractor 使用独立 10000 上限。
- 网页摘要移除隐藏的 60 秒 `wait_for`，统一使用 SDK timeout。
- 网页摘要通过 LangChain `abatch(max_concurrency=...)` 限制并发。
- 传输异常增加安全的 `cause_type` 和枚举化 `transport_reason`。
- 预算回调识别 OpenAI `max_completion_tokens`。
- 范围内完全没有结论的对象确定性补充 `Unknown`，并补充覆盖全部对象的比较缺口。
- CLI 接通候选来源展示、添加/拒绝、名称修正和二次范围确认。

## 5. 测试命令与结果

离线质量门禁：

```bash
uv run ruff check .
uv run mypy src/opencompete
uv run pytest -q
git diff --check
```

结果：Ruff 通过；mypy 通过；pytest `263 passed, 1 deselected`；diff 检查通过。

主供应商长文本流式探测：

```bash
uv run python - <主供应商流式探测脚本>
```

结果：首 chunk 21.832 秒，5376 chunks，最大 chunk 间隔 21.832 秒，聚合字符数 8010，总耗时 122.798 秒。

DeepSeek 长文本流式探测：

```bash
uv run python - <DeepSeek 流式探测脚本>
```

结果：首 chunk 11.808 秒，3985 chunks，最大 chunk 间隔 11.808 秒，聚合字符数 6565，总耗时 56.250 秒。

完整 live 测试：

```bash
OPENCOMPETE_LIVE_DATA_DIR=.opencompete-data/live-tests/2026-07-16-complete-3 \
uv run pytest -q -m live tests/opencompete/test_live_full_report.py -s
```

结果：`1 passed, 1 deselected`，耗时 655.08 秒；本次任务没有 `model_retry_scheduled` 事件。

## 6. 真实产物

- Source：33
- Evidence：41
- Claim：15
- Comparison：4
- 研究对象：3
- 报告字符数：11233
- 追溯违规：0
- 模型自动重试：0

报告：`.opencompete-data/live-tests/2026-07-16-complete-3/runs/226dd316-0959-4156-b2b0-085df48869f0/report.md`

Bundle：`.opencompete-data/live-tests/2026-07-16-complete-3/runs/226dd316-0959-4156-b2b0-085df48869f0/bundle.json`

事件：`.opencompete-data/live-tests/2026-07-16-complete-3/runs/226dd316-0959-4156-b2b0-085df48869f0/events.jsonl`

## 7. 已知限制与取舍

当前主备 `ModelRuntime` 仍包在 Graph 执行外层。Checkpoint 能从失败 superstep 恢复，但并行单元之一发生临时错误时，同一 superstep 中的成功兄弟调用仍可能被重放。此次可用性修复不同时重写全部结构化、工具和文本模型 Runnable；后续应基于 LangChain `with_retry`、`with_fallbacks` 把主备边界下移到单模型调用。

这项限制不影响本次完整任务通过，但会影响极端传输故障下的重复调用成本。SDK 隐式重试继续保持为 0，事件账本保留全部真实 attempt，不能把用户主动 `resume` 后的新执行窗口宣称为同一逻辑调用终身只有五次。
