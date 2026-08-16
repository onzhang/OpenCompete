<script setup>
import { computed } from 'vue'
import { STATUS_LABELS } from '../utils/format.js'

const props = defineProps({
  status: { type: String, required: true },
  size: { type: String, default: 'md' }, // sm | md | lg
})

const label = computed(() => STATUS_LABELS[props.status] || props.status)
</script>

<template>
  <span class="stamp" :class="[`stamp--${status}`, `stamp--${size}`]">
    {{ label }}
  </span>
</template>

<style scoped>
/* 印章式状态徽标 —— 双线框、微旋转、油墨叠印 */
.stamp {
  display: inline-block;
  font-family: var(--font-serif);
  font-weight: 700;
  letter-spacing: 0.18em;
  padding: 3px 10px 3px 12px;
  border: 2px solid currentColor;
  border-radius: 3px;
  position: relative;
  transform: rotate(-2deg);
  mix-blend-mode: multiply;
  white-space: nowrap;
  line-height: 1.5;
}

.stamp::after {
  content: '';
  position: absolute;
  inset: 2px;
  border: 1px solid currentColor;
  border-radius: 1px;
  opacity: 0.55;
}

.stamp--sm {
  font-size: 10.5px;
  padding: 1px 7px 1px 9px;
  border-width: 1.5px;
  letter-spacing: 0.14em;
}

.stamp--md {
  font-size: 12px;
}

.stamp--lg {
  font-size: 15px;
  padding: 5px 14px 5px 16px;
}

.stamp--draft,
.stamp--cancelled {
  color: var(--ink-faint);
}

.stamp--awaiting_confirmation,
.stamp--partial {
  color: var(--amber);
}

.stamp--scope_frozen {
  color: var(--slate);
}

.stamp--researching,
.stamp--synthesizing {
  color: var(--amber-bright);
}

.stamp--completed {
  color: var(--forest);
}

.stamp--failed {
  color: var(--vermilion);
}
</style>
