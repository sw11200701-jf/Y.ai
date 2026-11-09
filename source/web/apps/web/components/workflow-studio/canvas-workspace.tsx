'use client';
import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import styles from './studio.module.css';

const preferenceKey = 'zhangpu.workflow.panels.v3';
type Side = 'library' | 'inspector';
const defaults = { library: .18, inspector: .44 };
/** Preferences contain geometry only, never workflow data or account information. */
export function CanvasWorkspace({ library, inspector, children, inspectorOpen, onInspectorOpenChange }: { library: ReactNode; inspector: ReactNode; children: ReactNode; inspectorOpen?: boolean; onInspectorOpenChange?: (open: boolean) => void }) {
  const root = useRef<HTMLDivElement>(null);
  const [ratios, setRatios] = useState(defaults);
  const [collapsed, setClosed] = useState({ library: false, inspector: true });
  const [total, setTotal] = useState(1200);
  const compact = total < 720;
  const inspectorChange = useRef(onInspectorOpenChange); inspectorChange.current = onInspectorOpenChange;
  const closed = { ...collapsed, inspector: inspectorOpen === undefined ? collapsed.inspector : !inspectorOpen };
  const cleanup = useRef<() => void>(() => {});
  const loaded = useRef(false);
  useEffect(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(preferenceKey) || localStorage.getItem('zhangpu.workflow.panels.v2') || 'null');
      if (saved && ['library', 'inspector'].every(side => Number.isFinite(saved[side]) && saved[side] >= .1 && saved[side] <= .7)) {
        setRatios({ library: saved.library, inspector: Math.max(.38,saved.inspector) });
        if (typeof saved.libraryCollapsed === 'boolean') setClosed(previous => ({ ...previous, library: saved.libraryCollapsed }));
      }
    } catch { /* Unavailable or old preferences do not block the editor. */ }
    loaded.current = true;
    const element = root.current; let narrow = false;
    const measure = () => {
      const width = element?.clientWidth || 1200; const next = width < 960;
      setTotal(width);
      if (next && !narrow) { setClosed({ library: true, inspector: true }); inspectorChange.current?.(false); }
      narrow = next;
    };
    measure(); const observer = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(measure) : null;
    if (element) observer?.observe(element);
    return () => { cleanup.current(); observer?.disconnect(); };
  }, []);
  useEffect(() => {
    if (!loaded.current) return;
    try { localStorage.setItem(preferenceKey, JSON.stringify({ ...ratios, libraryCollapsed: collapsed.library })); } catch { /* Optional preference. */ }
  }, [ratios, collapsed.library]);
  useEffect(() => { if (compact && inspectorOpen) setClosed(previous => ({ ...previous, library: true })); }, [compact, inspectorOpen]);
  // Both columns shrink proportionally before either can consume the center.
  const wanted = { library: closed.library ? 36 : Math.max(180, Math.min(320, total * ratios.library)), inspector: closed.inspector ? 36 : Math.max(360, Math.min(760, total * ratios.inspector)) };
  const budget = Math.max(72, total - Math.min(300, total - 84) - 12);
  const excess = Math.max(0, wanted.library + wanted.inspector - budget);
  const flexible = (closed.library ? 0 : wanted.library - 36) + (closed.inspector ? 0 : wanted.inspector - 36);
  const widths = { library: wanted.library - (closed.library || !flexible ? 0 : excess * (wanted.library - 36) / flexible), inspector: wanted.inspector - (closed.inspector || !flexible ? 0 : excess * (wanted.inspector - 36) / flexible) };
  const toggle = (side: Side) => {
    const next = !closed[side];
    setClosed(previous => ({ ...previous, [side]: next, ...(compact && !next ? { [side === 'library' ? 'inspector' : 'library']: true } : {}) }));
    if (side === 'inspector') inspectorChange.current?.(!next);
    else if (compact && !next) inspectorChange.current?.(false);
  };
  const maximum = (side: Side) => Math.max(160, Math.min(side==='inspector'?760:320, total - widths[side === 'library' ? 'inspector' : 'library'] - 312));
  const resize = (side: Side, width: number) => {
    setRatios(previous => ({ ...previous, [side]: Math.max(160, Math.min(maximum(side), width)) / total }));
  };
  const reset = () => { cleanup.current(); setRatios(defaults); setClosed({ library: false, inspector: true }); inspectorChange.current?.(false); };
  const separator = (side: Side) => <div role="separator" aria-label={side === 'library' ? '调整节点库宽度' : '调整属性区宽度'} aria-orientation="vertical" aria-valuemin={160} aria-valuemax={maximum(side)} aria-valuenow={Math.round(widths[side])} tabIndex={closed[side] || compact ? -1 : 0} className={styles.splitter}
    onDoubleClick={() => setRatios(previous => ({ ...previous, [side]: defaults[side] }))}
    onKeyDown={e => { if (closed[side] || compact) return; if (e.key === 'Home') { e.preventDefault(); setRatios(previous => ({ ...previous, [side]: defaults[side] })); } else if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') { e.preventDefault(); resize(side, widths[side] + (e.key === 'ArrowRight' ? 20 : -20) * (side === 'library' ? 1 : -1)); } }}
    onPointerDown={e => {
      if (closed[side] || compact) return; e.preventDefault(); cleanup.current(); const start = e.clientX; const width = widths[side]; const pointerId = e.pointerId;
      const move = (event: PointerEvent) => { if (event.pointerId === pointerId) resize(side, width + (event.clientX - start) * (side === 'library' ? 1 : -1)); };
      const stop = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', stop); window.removeEventListener('pointercancel', stop); window.removeEventListener('blur', stop); };
      window.addEventListener('pointermove', move); window.addEventListener('pointerup', stop); window.addEventListener('pointercancel', stop); window.addEventListener('blur', stop); cleanup.current = stop;
    }} />;
  const panel = (side: Side, content: ReactNode) => <section className={styles.sidePanel} data-side={side} data-drawer={compact && !closed[side]} data-collapsed={closed[side]}><button className={styles.panelToggle} aria-label={`${closed[side] ? '展开' : '收起'}${side === 'library' ? '节点库' : '属性区'}`} aria-expanded={!closed[side]} onClick={() => toggle(side)}>{closed[side] ? (side === 'library' ? '节点库 ›' : '‹ 属性') : compact ? '← 返回画布' : (side === 'library' ? '‹ 收起节点库' : '收起属性区 ›')}</button><div className={styles.panelContent} hidden={closed[side]}>{content}</div>{side === 'library' && !closed.library && <button className={styles.layoutReset} onClick={reset}>重置侧栏布局</button>}</section>;
  return <div ref={root} className={`${styles.workspace} ${styles.resizableWorkspace}`} data-compact={compact} style={{ '--library-width': `${closed.library || compact ? 36 : widths.library}px`, '--inspector-width': `${closed.inspector || compact ? 36 : widths.inspector}px` } as CSSProperties}>{panel('library', library)}{separator('library')}{children}{separator('inspector')}{panel('inspector', inspector)}</div>;
}
