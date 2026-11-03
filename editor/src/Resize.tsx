// Resize handles for the selected element of the CV (a section, a job, a heading, a bullet…): drag the right edge for
// its width, the bottom edge for its height. Columns resize the sidebar; photos and logos resize the image.
import { useLayoutEffect, useState } from 'react';
import type { CvData, ElemStyle } from './types';
import { KINDS } from './design';
import { blurFields } from './Free';
import { selectorFor } from './Toolbar';

type Update = (fn: (d: CvData) => void) => void;
const PX_TO_MM = 25.4 / 96;
type Edge = 'e' | 'w' | 's' | 'se';

interface Box { l: number; t: number; w: number; h: number }

export function BoxHandles({ k, i, styleKey, data, update, checkpoint }: {
  k: string;
  i?: string;
  /** Where the size is stored: the kind ("all of them") or kind#id ("only this one"). */
  styleKey: string;
  data: CvData;
  update: Update;
  checkpoint: () => void;
}) {
  const [box, setBox] = useState<Box | null>(null);
  const [label, setLabel] = useState('');

  // Follow the element after every render (its size changes as you type, restyle or zoom).
  useLayoutEffect(() => {
    const node = document.querySelector<HTMLElement>(selectorFor(k, i));
    const page = document.querySelector<HTMLElement>('.canvas .page');
    if (!node || !page || k === 'page') return void (box && setBox(null));
    const pr = page.getBoundingClientRect();
    const r = node.getBoundingClientRect();
    const scale = pr.width / page.offsetWidth;
    const next = { l: (r.left - pr.left) / scale, t: (r.top - pr.top) / scale, w: r.width / scale, h: r.height / scale };
    if (!box || Math.abs(box.l - next.l) + Math.abs(box.t - next.t) + Math.abs(box.w - next.w) + Math.abs(box.h - next.h) > 0.5) setBox(next);
  });
  if (!box) return null;

  const L = data.design.layout;
  const cat = KINDS[k]?.cat ?? 'box';
  const column = k === 'sideCol' || k === 'mainCol';
  // The sidebar's inner edge is the one that moves.
  const sideOnRight = L.sidebar !== 'left';
  const edges: Edge[] = column
    ? [(k === 'sideCol') === sideOnRight ? 'w' : 'e']
    : cat === 'image'
      ? ['se']
      : ['e', 's', 'se'];

  const start = (e: React.PointerEvent, edge: Edge) => {
    e.preventDefault();
    e.stopPropagation();
    blurFields();
    const page = document.querySelector<HTMLElement>('.canvas .page');
    if (!page) return;
    const mm = 210 / page.getBoundingClientRect().width; // mm per screen pixel at any zoom
    const w0 = box.w * PX_TO_MM;
    const h0 = box.h * PX_TO_MM;
    const sx = e.clientX;
    const sy = e.clientY;
    checkpoint();
    const onMove = (ev: PointerEvent) => {
      const dx = (ev.clientX - sx) * mm;
      const dy = (ev.clientY - sy) * mm;
      const w = Math.max(3, edge === 'w' ? w0 - dx : edge === 's' ? w0 : w0 + dx);
      const h = Math.max(2, edge === 'e' || edge === 'w' ? h0 : h0 + dy);
      const r1 = (n: number) => Math.round(n * 10) / 10;
      if (column) {
        const sideMm = k === 'sideCol' ? w : 210 - w;
        const pct = Math.min(60, Math.max(15, (sideMm / 210) * 100));
        setLabel(`Sidebar ${r1(pct)}%`);
        update((d) => void (d.style.sidebarWidth = r1(pct)));
        return;
      }
      if (cat === 'image') {
        const size = r1(Math.max(w, h));
        setLabel(`${size} × ${size} mm`);
        update((d) => void (d.design.elements[styleKey] = { ...(d.design.elements[styleKey] ?? {}), imgSize: size }));
        return;
      }
      const patch: ElemStyle = {};
      if (edge !== 's') patch.width = r1(w);
      if (edge !== 'e') patch.height = r1(h);
      setLabel(`${r1(w)} × ${r1(h)} mm`);
      update((d) => void (d.design.elements[styleKey] = { ...(d.design.elements[styleKey] ?? {}), ...patch }));
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      setLabel('');
      checkpoint();
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  };

  return (
    <div className="box-handles" style={{ left: box.l, top: box.t, width: box.w, height: box.h }}>
      {edges.map((edge) => (
        <span
          key={edge}
          className={`bh bh-${edge}`}
          title={column ? 'Drag to change the sidebar width' : edge === 's' ? 'Drag to change the height' : edge === 'se' ? 'Drag to change the size' : 'Drag to change the width'}
          onPointerDown={(e) => start(e, edge)}
        />
      ))}
      {label && <span className="bh-label">{label}</span>}
    </div>
  );
}
