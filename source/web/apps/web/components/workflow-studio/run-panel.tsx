'use client';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import styles from './studio.module.css';

const preferenceKey = 'zhangpu.workflow.run-height.v1';
/** Resizes within the center column; never changes the graph or its viewport. */
export function RunPanel({ children }: { children: ReactNode }) {
  const panel = useRef<HTMLElement>(null);
  const [ratio, setRatio] = useState(.26);
  const [available, setAvailable] = useState(700);
  const cleanup = useRef<() => void>(() => {});
  useEffect(() => {
    try { const saved = Number(localStorage.getItem(preferenceKey)); if (saved >= .15 && saved <= .65) setRatio(saved); } catch { /* Optional geometry preference. */ }
    const parent = panel.current?.parentElement;
    const measure = () => setAvailable(parent?.clientHeight || 700);
    measure(); const observer = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(measure) : null;
    if (parent) observer?.observe(parent);
    return () => { cleanup.current(); observer?.disconnect(); };
  }, []);
  const minimum = Math.min(120, available * .3);
  const maximum = Math.max(minimum, available - 190);
  const height = Math.max(minimum, Math.min(maximum, available * ratio));
  const resize = (next: number) => {
    const value = Math.max(minimum, Math.min(maximum, next)) / available;
    setRatio(value); try { localStorage.setItem(preferenceKey, String(value)); } catch { /* Optional geometry preference. */ }
  };
  return <section ref={panel} className={`${styles.results} ${styles.runPanel}`} style={{ height }}>
    <div role="separator" aria-label="调整试运行区高度" aria-orientation="horizontal" aria-valuemin={Math.round(minimum)} aria-valuemax={Math.round(maximum)} aria-valuenow={Math.round(height)} className={styles.horizontalSplitter} tabIndex={0}
      onDoubleClick={() => resize(available * .26)}
      onKeyDown={e => { if (e.key === 'ArrowUp' || e.key === 'ArrowDown' || e.key === 'Home') { e.preventDefault(); resize(e.key === 'Home' ? available * .26 : height + (e.key === 'ArrowUp' ? 20 : -20)); } }}
      onPointerDown={e => {
        e.preventDefault(); cleanup.current(); const start = e.clientY; const initial = height; const pointerId = e.pointerId;
        const move = (event: PointerEvent) => { if (event.pointerId === pointerId) resize(initial + start - event.clientY); };
        const stop = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', stop); window.removeEventListener('pointercancel', stop); window.removeEventListener('blur', stop); };
        window.addEventListener('pointermove', move); window.addEventListener('pointerup', stop); window.addEventListener('pointercancel', stop); window.addEventListener('blur', stop); cleanup.current = stop;
      }}><span /></div>
    <div className={styles.runPanelScroll}><button className={styles.layoutReset} onClick={() => resize(available * .26)}>重置试运行区高度</button>{children}</div>
  </section>;
}
