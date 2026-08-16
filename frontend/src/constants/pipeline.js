// 研究执行主链路常量：与后端 events.jsonl 的 node 字段对齐。
// researcher / compress_research 归入「并行研究」段。

export const TRACK = [
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
  freeze_scope: 0,
  write_research_brief: 1,
  supervisor: 2,
  researcher: 3,
  compress_research: 3,
  final_report_generation: 4,
  materialize: 5,
  validate: 6,
}

// 并行研究段在 TRACK 中的下标（PipelineTrack 渲染支线用）
export const PARALLEL_TRACK_INDEX = 3
