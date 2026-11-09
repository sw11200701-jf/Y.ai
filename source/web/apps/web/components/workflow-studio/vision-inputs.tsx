'use client';
import type { ModelChoice } from '@/lib/model-catalog';
import type { MessageDraft, ObjectValue, VariableOption, WorkflowDefinition } from '@/lib/workflow/types';
import { currentImages, modelVisionProfile, visionSources } from '@/lib/workflow/vision-input';
import styles from './studio.module.css';

export function VisionInputs({ config, inputs, model, contract, workflow, variables, message, onConfig, onInputs }: { config: ObjectValue; inputs: ObjectValue; model?: ModelChoice; contract: boolean; workflow?: WorkflowDefinition; variables: VariableOption[]; message?: MessageDraft; onConfig: (patch: ObjectValue) => void; onInputs: (next: ObjectValue) => void }) {
  const profile = modelVisionProfile(model), images = currentImages(message), sources = visionSources(workflow, variables), selected = Array.isArray(inputs.image_attachment_ids) ? inputs.image_attachment_ids : [];
  const old = selected.filter(id => !images.some(item => item.id === id));
  const source = inputs.image_attachment_ids?.$ref || inputs.message?.$ref || '';
  return <section className={styles.editorSection}><h3>视觉理解输入</h3><label className={styles.check}><input aria-label="启用LLM图片输入" type="checkbox" checked={config.vision_enabled === true} disabled={config.vision_enabled !== true && (!contract || !profile)} onChange={event => onConfig({ vision_enabled: event.target.checked })} />使用本次消息中的图片</label>
    {!contract && <p className={styles.hint}>当前服务尚未声明视觉节点合同；已有配置保留，不能在旧服务上宣称可执行。</p>}{!profile && <p className={styles.hint}>请先选择有明确版本视觉声明的模型。声明仍需服务端核对模型ID和当前权限，不代表实际识图质量已验证。</p>}
    {profile && <p className={styles.hint}>模型视觉声明：{profile.model_revision} · 仅本次消息受控图片；管理员声明，尚非供应商实测证明。</p>}
    {config.vision_enabled === true && <><p className={styles.hint}>仅LLM支持。选择1至4张PNG/JPEG/WebP单帧图片；单张≤2MiB、总量≤4MiB，每张≤1MP且最长边≤2048。像素、格式、当前run/附件权限和上下文预算由服务器在执行前复验，不自动缩放或裁切。音频和Agent视觉尚未开放。</p>
      <h4>当前待受理消息的图片</h4>{images.length ? images.map(item => <label key={item.id} className={styles.check}><input type="checkbox" aria-label={`视觉图片：${item.filename}`} checked={selected.includes(item.id)} disabled={!selected.includes(item.id) && selected.length >= 4} onChange={event => onInputs({ ...inputs, image_attachment_ids: event.target.checked ? [...selected, item.id] : selected.filter(id => id !== item.id) })} />{item.filename} · {(item.size_bytes / 1024).toFixed(0)}KiB</label>) : <p className={styles.hint}>请在试运行输入区通过现有消息上传入口添加图片；保存节点本身不会上传或发送图片。</p>}
      {old.length > 0 && <p role="alert">{old.length}张已保存图片不在本次消息中。<button onClick={() => onInputs({ ...inputs, image_attachment_ids: selected.filter(id => images.some(item => item.id === id)) })}>明确移除旧图片选择</button></p>}
      <label className={styles.field}><span>或选择已连接的图片变量</span><select aria-label="视觉图片变量来源" value={source} onChange={event => { const item = sources.find(item => item.ref === event.target.value); if (!item) return; const next = { ...inputs }; if (item.kind === 'message') { next.message = { $ref: item.ref }; delete next.image_attachment_ids; } else next.image_attachment_ids = { $ref: item.ref }; onInputs(next); }}><option value="">请选择来源（不手填ID或路径）</option>{source && !sources.some(item => item.ref === source) && <option value={source}>已保存来源（当前无法确认图片类型）</option>}{sources.map(item => <option key={item.ref} value={item.ref}>{item.label} · {item.kind === 'message' ? '消息中的图片' : '图片附件列表'}</option>)}</select></label>
      <p className={styles.hint}>上游变量只声明来源，不替代本次消息受理与权限校验。混合消息通过消息对象提取图片，不把其中所有附件当图片。</p></>}
    {(Object.hasOwn(inputs, 'image_attachment_ids') || sources.some(item => item.kind === 'message' && item.ref === inputs.message?.$ref)) && <button onClick={() => { const next = { ...inputs }; delete next.image_attachment_ids; if (sources.some(item => item.kind === 'message' && item.ref === next.message?.$ref)) delete next.message; onInputs(next); }}>明确移除图片选择和图片消息来源</button>}
  </section>;
}
