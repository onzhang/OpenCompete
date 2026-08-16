<script setup>
import { ref } from 'vue'

const props = defineProps({
  modelValue: { type: Array, default: () => [] },
  placeholder: { type: String, default: '输入后回车添加' },
  accent: { type: Boolean, default: false },
  validate: { type: Function, default: null },
})

const emit = defineEmits(['update:modelValue'])
const draft = ref('')
const error = ref('')

function commit() {
  const raw = draft.value.trim()
  if (!raw) return
  const pieces = raw
    .replace(/，/g, ',')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean)
  const next = [...props.modelValue]
  for (const p of pieces) {
    if (props.validate) {
      const msg = props.validate(p)
      if (msg) {
        error.value = msg
        return
      }
    }
    if (!next.includes(p)) next.push(p)
  }
  error.value = ''
  draft.value = ''
  emit('update:modelValue', next)
}

function remove(index) {
  const next = props.modelValue.slice()
  next.splice(index, 1)
  emit('update:modelValue', next)
}

function onBackspace() {
  if (!draft.value && props.modelValue.length) {
    remove(props.modelValue.length - 1)
  }
}
</script>

<template>
  <div>
    <div class="tags" :class="{ 'tags--error': error }">
      <span
        v-for="(tag, i) in modelValue"
        :key="tag"
        class="tag"
        :class="{ 'tag--accent': accent }"
      >
        {{ tag }}
        <button type="button" class="tag-x" @click="remove(i)" aria-label="移除">×</button>
      </span>
      <input
        v-model="draft"
        class="tag-input"
        :placeholder="modelValue.length ? '' : placeholder"
        @keydown.enter.prevent="commit"
        @keydown.backspace="onBackspace"
        @blur="commit"
      />
    </div>
    <div v-if="error" class="tag-error">{{ error }}</div>
  </div>
</template>

<style scoped>
.tags {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
  padding: 8px 10px;
  background: var(--paper-raised);
  border: 1.5px solid var(--line-strong);
  border-radius: var(--radius);
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
  cursor: text;
}

.tags:focus-within {
  border-color: var(--ink);
  box-shadow: 3px 3px 0 rgba(28, 33, 29, 0.16);
}

.tags--error {
  border-color: var(--vermilion);
}

.tag {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 6px 3px 11px;
  font-size: 13px;
  font-weight: 500;
  background: var(--paper-dim);
  border: 1px solid var(--line-strong);
  border-radius: 3px;
}

.tag--accent {
  background: var(--vermilion-wash);
  border-color: var(--vermilion);
  color: var(--vermilion-deep);
  font-weight: 700;
}

.tag-x {
  border: none;
  background: transparent;
  font-size: 15px;
  line-height: 1;
  color: var(--ink-faint);
  padding: 1px 4px;
  border-radius: 2px;
}

.tag-x:hover {
  color: var(--vermilion);
  background: rgba(194, 64, 28, 0.1);
}

.tag-input {
  flex: 1;
  min-width: 140px;
  border: none;
  background: transparent;
  font-size: 13.5px;
  padding: 4px 2px;
  color: var(--ink);
}

.tag-input:focus {
  outline: none;
}

.tag-input::placeholder {
  color: var(--ink-ghost);
}

.tag-error {
  margin-top: 6px;
  font-size: 12px;
  color: var(--vermilion);
}
</style>
