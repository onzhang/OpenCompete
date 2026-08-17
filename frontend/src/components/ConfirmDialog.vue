<script setup>
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'

const props = defineProps({
  open: { type: Boolean, default: false },
  title: { type: String, required: true },
  subject: { type: String, default: '' },
  details: { type: Array, default: () => [] },
  confirmLabel: { type: String, default: '确认' },
  pendingLabel: { type: String, default: '处理中…' },
  pending: { type: Boolean, default: false },
  error: { type: String, default: '' },
  returnFocus: { type: Object, default: null },
})

const emit = defineEmits(['cancel', 'confirm'])
const safeAction = ref(null)
let lastFocused = null

function close() {
  if (!props.pending) emit('cancel')
}

function onKeydown(event) {
  if (event.key === 'Escape') {
    event.preventDefault()
    close()
  }
}

watch(
  () => props.open,
  async (open) => {
    if (open) {
      lastFocused = document.activeElement
      await nextTick()
      safeAction.value?.focus()
      document.addEventListener('keydown', onKeydown)
      return
    }
    document.removeEventListener('keydown', onKeydown)
    const target = props.returnFocus || lastFocused
    target?.focus?.()
    lastFocused = null
  },
)

onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <Teleport to="body">
    <Transition name="confirm-dialog">
      <div v-if="open" class="confirm-backdrop" @click.self="close">
        <section
          class="confirm-dialog pressed"
          role="dialog"
          tabindex="-1"
          @keydown.esc.stop.prevent="close"
          aria-modal="true"
          :aria-labelledby="'confirm-dialog-title'"
          :aria-describedby="'confirm-dialog-details'"
        >
          <header class="confirm-head">
            <span class="confirm-mark mono">危险操作</span>
            <h2 id="confirm-dialog-title">{{ title }}</h2>
          </header>

          <div class="confirm-subject">{{ subject }}</div>
          <ul v-if="details.length" id="confirm-dialog-details" class="confirm-details">
            <li v-for="detail in details" :key="detail">{{ detail }}</li>
          </ul>

          <p v-if="error" class="confirm-error" role="alert">{{ error }}</p>

          <footer class="confirm-actions">
            <button
              ref="safeAction"
              type="button"
              class="btn btn--ghost"
              :disabled="pending"
              @click="close"
            >
              保留任务
            </button>
            <button
              type="button"
              class="btn btn--danger"
              :disabled="pending"
              @click="emit('confirm')"
            >
              {{ pending ? pendingLabel : confirmLabel }}
            </button>
          </footer>
        </section>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.confirm-backdrop {
  position: fixed;
  inset: 0;
  z-index: 100;
  display: grid;
  place-items: center;
  padding: 24px;
  background: rgba(28, 33, 29, 0.48);
  backdrop-filter: blur(2px);
}

.confirm-dialog {
  width: min(100%, 520px);
  padding: 26px 28px 24px;
  background: var(--paper-raised);
  border: 1.5px solid var(--ink);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-press), var(--shadow-paper);
}

.confirm-head {
  display: flex;
  align-items: baseline;
  gap: 12px;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--line);
}

.confirm-mark {
  color: var(--vermilion);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.confirm-head h2 {
  margin: 0;
  font-family: var(--font-serif);
  font-size: 21px;
  line-height: 1.25;
}

.confirm-subject {
  margin-top: 18px;
  color: var(--ink);
  font-family: var(--font-serif);
  font-size: 17px;
  font-weight: 700;
  line-height: 1.5;
  overflow-wrap: anywhere;
}

.confirm-details {
  display: grid;
  gap: 7px;
  margin: 16px 0 0;
  padding: 13px 15px 13px 32px;
  border-left: 3px solid var(--vermilion);
  background: var(--vermilion-wash);
  color: var(--ink-soft);
  font-size: 13px;
  line-height: 1.55;
}

.confirm-error {
  margin: 16px 0 0;
  padding: 10px 12px;
  border: 1px solid var(--vermilion);
  background: var(--vermilion-wash);
  color: var(--vermilion-deep);
  font-size: 13px;
  line-height: 1.55;
}

.confirm-actions {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  margin-top: 22px;
}

.btn--danger {
  background: var(--vermilion);
  color: #fdf6ee;
  border-color: var(--ink);
  box-shadow: var(--shadow-press-sm);
}

.btn--danger:hover:not(:disabled) {
  background: var(--vermilion-deep);
  transform: translate(-1px, -1px);
  box-shadow: 4px 4px 0 var(--ink);
}

.confirm-dialog-enter-active,
.confirm-dialog-leave-active {
  transition: opacity 0.16s ease;
}

.confirm-dialog-enter-active .confirm-dialog,
.confirm-dialog-leave-active .confirm-dialog {
  transition: opacity 0.16s ease, transform 0.16s ease;
}

.confirm-dialog-enter-from,
.confirm-dialog-leave-to {
  opacity: 0;
}

.confirm-dialog-enter-from .confirm-dialog,
.confirm-dialog-leave-to .confirm-dialog {
  opacity: 0;
  transform: translateY(12px);
}

@media (max-width: 560px) {
  .confirm-backdrop {
    align-items: end;
    padding: 14px;
  }

  .confirm-dialog {
    padding: 22px 20px 20px;
  }

  .confirm-actions {
    flex-direction: column-reverse;
  }

  .confirm-actions .btn {
    width: 100%;
  }
}
</style>
