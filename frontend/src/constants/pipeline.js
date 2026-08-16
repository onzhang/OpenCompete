// 研究执行主链路常量：与后端 events.jsonl 的 node 字段对齐。
// 第 0 阶段覆盖 draft 期的两类调用：prepare_scope（范围规划）与
// discovery（候选发现检索）；不带候选发现的任务同样从第 0 阶段推进。
// researcher / compress_research 归入「并行研究」段。

export const TRACK = [
  { key: 'discovery', label: '候选与范围' },
  { key: 'freeze_scope', label: '范围冻结' },
  { key: 'write_research_brief', label: '研究简报' },
  { key: 'supervisor', label: '规划分派' },
  { key: 'parallel_research', label: '并行研究' },
  { key: 'final_report_generation', label: '撰写报告' },
  { key: 'materialize', label: '结构化产物' },
  { key: 'validate', label: '门禁校验' },
  { key: 'published', label: '发布' },
]

export const NODE_TO_TRACK = {
  prepare_scope: 0,
  discovery: 0,
  freeze_scope: 1,
  write_research_brief: 2,
  supervisor: 3,
  researcher: 4,
  compress_research: 4,
  final_report_generation: 5,
  materialize: 6,
  validate: 7,
}

// 并行研究段在 TRACK 中的下标（PipelineTrack 渲染支线用）
export const PARALLEL_TRACK_INDEX = 4

// 发布阶段的轨道下标（run_published 事件驱动，无独立 node）
export const PUBLISHED_TRACK_INDEX = TRACK.length - 1
