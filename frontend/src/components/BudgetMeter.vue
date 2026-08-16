<script setup>
import { computed } from 'vue'
import { fmtTokens, fmtUsd } from '../utils/format.js'

const props = defineProps({
  label: { type: String, required: true },
  spent: { type: Number, required: true },
  reserved: { type: Number, default: 0 },
  limit: { type: Number, required: true },
  kind: { type: String, default: 'tokens' }, // tokens | calls | usd
})

const spentRatio = computed(() =>
  props.limit > 0 ? Math.min(1, props.spent / props.limit) : 0,
)
const reservedRatio = computed(() =>
  props.limit > 0 ? Math.min(1 - spentRatio.value, Math.max(0, props.reserved) / props.limit) : 0,
)

const level = computed(() => {
  if (spentRatio.value >= 0.95) return 'danger'
  if (spentRatio.value >= 0.8) return 'warn'
  return 'ok'
})

function fmt(n) {
  if (props.kind === 'usd') return fmtUsd(n)
  if (props.kind === 'calls') return String(Math.round(n))
  return fmtTokens(n)
}
</script>

<template>
  <div class="meter">
    <div class="meter-head">
      <span class="meter-label">{{ label }}</span>
      <span class="meter-value mono">
        <b :class="`val--${level}`">{{ fmt(spent) }}</b>
        <span class="meter-limit">/ {{ fmt(limit) }}</span>
      </span>
    </div>
    <div class="meter-track">
      <div class="meter-fill" :class="`fill--${level}`" :style="{ width: `${spentRatio * 100}%` }"></div>
      <div
        v-if="reservedRatio > 0.002"
        class="meter-reserved"
        :style="{ left: `${spentRatio * 100}%`, width: `${reservedRatio * 100}%` }"
      ></div>
    </div>
  </div>
</template>

<style scoped>
.meter-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 5px;
}

.meter-label {
  font-size: 12px;
  font-weight: 500;
  color: var(--ink-soft);
  letter-spacing: 0.04em;
}

.meter-value {
  font-size: 12.5px;
}

.meter-value b {
  font-weight: 600;
}

.val--ok { color: var(--ink); }
.val--warn { color: var(--amber); }
.val--danger { color: var(--vermilion); }

.meter-limit {
  color: var(--ink-faint);
  font-size: 11px;
  margin-left: 3px;
}

.meter-track {
  position: relative;
  height: 7px;
  background: var(--paper-dim);
  border: 1px solid var(--line);
  border-radius: 4px;
  overflow: hidden;
}

.meter-fill {
  position: absolute;
  inset: 0 auto 0 0;
  border-radius: 3px 0 0 3px;
  transition: width 0.5s cubic-bezier(0.22, 0.8, 0.3, 1);
}

.fill--ok { background: var(--forest); }
.fill--warn { background: var(--amber-bright); }
.fill--danger { background: var(--vermilion); }

/* 预留段 —— 斜纹表示尚未结算 */
.meter-reserved {
  position: absolute;
  top: 0;
  bottom: 0;
  background: repeating-linear-gradient(
    -45deg,
    rgba(168, 115, 15, 0.55) 0 3px,
    transparent 3px 6px
  );
  transition: left 0.5s ease, width 0.5s ease;
}
</style>
