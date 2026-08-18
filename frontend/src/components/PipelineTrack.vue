<script setup>
import { computed } from 'vue'
import { TRACK, PARALLEL_TRACK_INDEX } from '../constants/pipeline.js'

const props = defineProps({
  trackIndex: { type: Number, default: 0 },
  finished: { type: Boolean, default: false },
  units: { type: Array, default: () => [] },
  unitStates: { type: Object, default: () => ({}) },
})

const PARALLEL_IDX = PARALLEL_TRACK_INDEX

function nodeState(i) {
  if (props.finished) return 'done'
  if (i < props.trackIndex) return 'done'
  if (i === props.trackIndex) return 'active'
  return 'pending'
}

const stateOfUnit = computed(() => (unitId) => props.unitStates[unitId] || 'pending')

const UNIT_STATE_LABELS = {
  pending: '待分派',
  searching: '搜索取证中',
  compressing: '整理证据中',
  retrying: '重试中',
  done: '已完成',
}

const track = TRACK
</script>

<template>
  <div class="pipeline">
    <div class="track">
      <template v-for="(node, i) in track" :key="node.key">
        <div class="node" :class="`node--${nodeState(i)}`">
          <div class="node-dot">
            <svg v-if="nodeState(i) === 'done'" viewBox="0 0 12 12" class="node-check">
              <path d="M2.5 6.5 5 9l4.5-5.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
          </div>
          <div class="node-label">{{ node.label }}</div>
          <div v-if="i === PARALLEL_IDX && units.length" class="node-badge mono">×{{ units.length }}</div>
        </div>
        <div v-if="i < track.length - 1" class="link" :class="{ 'link--done': nodeState(i) === 'done' }"></div>
      </template>
    </div>

    <!-- 并行研究单元支线 -->
    <div v-if="units.length" class="units">
      <div class="units-rail"></div>
      <div
        v-for="unit in units"
        :key="unit.unit_id"
        class="unit"
        :class="`unit--${stateOfUnit(unit.unit_id)}`"
      >
        <span class="unit-dot"></span>
        <span class="unit-topic" :title="unit.topic">{{ unit.topic }}</span>
        <span class="unit-state">{{ UNIT_STATE_LABELS[stateOfUnit(unit.unit_id)] }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.track {
  display: flex;
  align-items: flex-start;
  padding: 6px 2px 0;
}

.node {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  min-width: 64px;
  position: relative;
}

.node-dot {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  border: 2px solid var(--line-strong);
  background: var(--paper-raised);
  display: grid;
  place-items: center;
  transition: all 0.3s ease;
}

.node-check {
  width: 12px;
  height: 12px;
  color: var(--paper-raised);
}

.node--done .node-dot {
  background: var(--forest);
  border-color: var(--forest);
}

.node--active .node-dot {
  background: var(--amber-bright);
  border-color: var(--amber-bright);
  animation: pulse-ring 1.6s infinite;
}

.node--pending .node-dot {
  background: var(--paper-raised);
}

.node-label {
  font-size: 11.5px;
  font-weight: 500;
  color: var(--ink-faint);
  white-space: nowrap;
  letter-spacing: 0.02em;
}

.node--done .node-label {
  color: var(--ink-soft);
}

.node--active .node-label {
  color: var(--ink);
  font-weight: 700;
}

.node-badge {
  position: absolute;
  top: -9px;
  right: 4px;
  font-size: 9px;
  font-weight: 600;
  color: var(--paper-raised);
  background: var(--slate);
  border-radius: 8px;
  padding: 0 5px;
  line-height: 1.5;
}

.link {
  flex: 1;
  height: 2px;
  min-width: 14px;
  margin-top: 11px;
  background: repeating-linear-gradient(
    90deg,
    var(--line-strong) 0 5px,
    transparent 5px 9px
  );
  transition: background 0.3s ease;
}

.link--done {
  background: var(--forest);
}

/* ============ 并行单元支线 ============ */
.units {
  margin: 14px 0 2px 34px;
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 7px;
  padding-left: 18px;
}

.units-rail {
  position: absolute;
  left: 0;
  top: 4px;
  bottom: 4px;
  width: 2px;
  background: var(--line-strong);
  border-radius: 1px;
}

.unit {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12.5px;
  padding: 7px 12px;
  background: var(--paper-dim);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  position: relative;
}

.unit::before {
  content: '';
  position: absolute;
  left: -18px;
  top: 50%;
  width: 18px;
  height: 2px;
  background: var(--line-strong);
}

.unit-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--ink-ghost);
  flex-shrink: 0;
}

.unit--searching .unit-dot,
.unit--compressing .unit-dot {
  background: var(--amber-bright);
  animation: pulse-ring 1.6s infinite;
}

.unit--retrying .unit-dot {
  background: var(--vermilion);
  animation: pulse-ring 1.1s infinite;
}

.unit--done .unit-dot {
  background: var(--forest);
}

.unit-topic {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--ink-soft);
}

.unit--done .unit-topic {
  color: var(--ink-faint);
}

.unit-state {
  font-size: 11px;
  font-weight: 600;
  color: var(--ink-faint);
  white-space: nowrap;
}

.unit--searching .unit-state,
.unit--compressing .unit-state {
  color: var(--amber);
}

.unit--retrying .unit-state {
  color: var(--vermilion);
}

.unit--done .unit-state {
  color: var(--forest);
}
</style>
