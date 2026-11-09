'use client';
import { useEffect, useRef, type RefObject } from 'react';
import { useNodesInitialized, useReactFlow } from '@xyflow/react';

export function canvasFitOptions(width: number, height: number) {
  const minimap = width >= 550 && height >= 280;
  return { padding: { top: '24px', left: '40px', right: minimap ? '150px' : '24px', bottom: minimap ? '110px' : '44px' }, maxZoom: 1, duration: 0 } as const;
}
/** Wait for measured nodes; programmatic fitting never edits the saved viewport. */
export function CanvasViewport({ container, session }: { container: RefObject<HTMLDivElement | null>; session: number }) {
  const ready = useNodesInitialized(); const { fitView } = useReactFlow();
  const fit = useRef(fitView); fit.current = fitView;
  const fitted = useRef<number | null>(null);
  useEffect(() => {
    const element = container.current; if (!ready || !element || fitted.current === session) return;
    let timer: ReturnType<typeof setTimeout>;
    const pointers = new Set<number>(); let pending = false;
    const schedule = () => { pending = true; clearTimeout(timer); if (pointers.size) return; timer = setTimeout(() => {
      if (pointers.size) return; pending = false;
      if (element.clientWidth <= 0 || element.clientHeight <= 0) return;
      fitted.current = session;
      void fit.current(canvasFitOptions(element.clientWidth, element.clientHeight));
    }, 80); };
    // Selection can expand the inspector before React Flow emits drag-start.
    // Capture pointer-down first: fitting between drag updates changes the
    // screen-to-flow transform and corrupts the node's next position update.
    const start = (event: PointerEvent) => { pointers.add(event.pointerId); clearTimeout(timer); };
    const end = (event: PointerEvent) => { pointers.delete(event.pointerId); if (!pointers.size && pending) schedule(); };
    element.addEventListener('pointerdown', start, true);
    window.addEventListener('pointerup', end, true); window.addEventListener('pointercancel', end, true);
    schedule();
    // Panel/window changes keep the screen-to-flow transform. Later fitting is explicit.
    return () => { clearTimeout(timer); element.removeEventListener('pointerdown', start, true); window.removeEventListener('pointerup', end, true); window.removeEventListener('pointercancel', end, true); };
  }, [container, ready, session]);
  return null;
}
