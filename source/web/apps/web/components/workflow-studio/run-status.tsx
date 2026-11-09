'use client';
import type { StudioRun } from '@/lib/workflow/types';
import styles from './studio.module.css';

const messages: Record<string, string> = {
  paused: '流程已暂停。请确认暂停原因，再由有权人员继续处理。',
  waiting_approval: '流程正在等待指定审批人处理，处理结果会显示在运行记录中。',
  resuming: '正在恢复流程，请等待状态更新。',
  outcome_unknown: '上一次操作的执行结果尚未确认，当前不会自动重试。请由有权人员核对，避免重复执行。',
};
/** Display product status only. Mutation controls require the server's exact action capability. */
export function RunStatus({ run }: { run: StudioRun }) {
  const text = messages[run.status];
  return <>{text && <div role="status" className={run.status === 'outcome_unknown' ? styles.warning : styles.notice}>{text}</div>}{typeof run.engine?.dsl_sha256 === 'string' && <details className={styles.details}><summary>本次流程快照标识</summary><code>{run.engine.dsl_sha256}</code></details>}{run.engine?.name !== 'langgraph' && run.status === 'interrupted' && <p className={styles.hint}>这是历史运行记录。请新建一次运行以继续处理。</p>}</>;
}
