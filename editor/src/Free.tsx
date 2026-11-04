// Free elements: text boxes, shapes, lines, icons and images placed anywhere on the page, Canva style.
// Select with a click, drag to move, handles to resize and rotate, double-click a text box to type.
import { createContext, useContext, useLayoutEffect, useState, type CSSProperties, type PointerEvent as RPointerEvent } from 'react';
import type { CvData, ElemStyle, FreeEl } from './types';
import { EditContext, Field } from './Editable';
import { iconSvg } from './icons';
import { uid } from './model';

type Update = (fn: (d: CvData) => void) => void;
export type Handle = 'move' | 'n' | 's' | 'e' | 'w' | 'ne' | 'nw' | 'se' | 'sw' | 'rot';

export interface Guides { x: number[]; y: number[] }

export interface CanvasCtx {
  active: boolean;
  selected: string | null;
  editing: string | null;
  guides: Guides | null;
  startDrag: (e: RPointerEvent, el: FreeEl, mode: Handle) => void;
  select: (id: string, shift?: boolean) => void;
  /** Free elements in a multiple selection (dragged together). */
  multi?: string[];
  startEdit: (id: string) => void;
  endEdit: () => void;
}

export const CanvasContext = createContext<CanvasCtx>({
  active: false, selected: null, editing: null, guides: null,
  startDrag: () => {}, select: () => {}, startEdit: () => {}, endEdit: () => {},
});

const PAGE_W = 210;
const PAGE_H = 297;
const PX_TO_MM = 25.4 / 96;

/** Inline CSS for a free text box's style. */
export function textCss(s: ElemStyle = {}): CSSProperties {
  return {
    fontFamily: s.font,
    fontSize: `calc(var(--fs) * ${s.size ?? 1})`,
    color: s.color,
    backgroundColor: s.bg,
    fontWeight: s.bold === undefined ? undefined : s.bold ? 700 : 400,
    fontStyle: s.italic ? 'italic' : undefined,
    textDecoration: s.underline ? 'underline' : undefined,
    textTransform: s.upper ? 'uppercase' : undefined,
    textAlign: s.align,
    letterSpacing: s.letterSpacing !== undefined ? `${s.letterSpacing}em` : undefined,
    lineHeight: s.lineHeight,
    padding: s.padding !== undefined ? `${s.padding}mm` : undefined,
    borderRadius: s.radius !== undefined ? `${s.radius}mm` : undefined,
    border: s.borderWidth ? `${s.borderWidth}mm solid ${s.borderColor || 'currentColor'}` : undefined,
  };
}

function boxCss(el: FreeEl, height: string): CSSProperties {
  return {
    left: `${el.x}mm`,
    top: `${el.y}mm`,
    width: `${el.w}mm`,
    height,
    transform: el.rot ? `rotate(${el.rot}deg)` : undefined,
  };
}

function Body({ el, update }: { el: FreeEl; update: Update }) {
  const ctx = useContext(CanvasContext);
  const opacity = el.opacity ?? 1;
  switch (el.type) {
    case 'text':
      return (
        <EditContext.Provider value={{ editable: ctx.editing === el.id, focus: () => {} }}>
          <Field
            id={`free:${el.id}`}
            as="div"
            rich
            multiline
            className="free-text"
            style={{ ...textCss(el.style), opacity }}
            value={el.text ?? ''}
            placeholder="Type…"
            onChange={(v) => update((d) => void (d.design.free.find((f) => f.id === el.id)!.text = v))}
            onDone={ctx.endEdit}
          />
        </EditContext.Provider>
      );
    case 'rect':
    case 'ellipse':
      return (
        <div
          className="free-shape"
          style={{
            background: el.fill || 'transparent',
            border: el.strokeWidth ? `${el.strokeWidth}mm ${el.dash || 'solid'} ${el.stroke || '#000'}` : undefined,
            borderRadius: el.type === 'ellipse' ? '50%' : `${el.radius ?? 0}mm`,
            opacity,
          }}
        />
      );
    case 'line':
      return <div className="free-line" style={{ borderTop: `${el.strokeWidth ?? 0.5}mm ${el.dash || 'solid'} ${el.stroke || '#000'}`, opacity }} />;
    case 'icon':
      return <div className="free-icon" style={{ color: el.fill || '#000', opacity }} dangerouslySetInnerHTML={{ __html: iconSvg(el.icon ?? 'star', el.strokeWidth ?? 2) }} />;
    case 'image':
      return el.src ? (
        <img
          className="free-image"
          src={el.src}
          alt=""
          draggable={false}
          style={{
            borderRadius: `${el.radius ?? 0}mm`,
            border: el.strokeWidth ? `${el.strokeWidth}mm solid ${el.stroke || '#000'}` : undefined,
            opacity,
          }}
        />
      ) : null;
  }
}

function Item({ el, update }: { el: FreeEl; update: Update }) {
  const ctx = useContext(CanvasContext);
  const editingThis = ctx.editing === el.id;
  const height = el.type === 'text' ? 'auto' : `${el.h}mm`;
  return (
    <div
      className={`free free-${el.type} ${editingThis ? 'editing' : ''}`}
      data-free={el.id}
      style={boxCss(el, height)}
      onPointerDown={
        ctx.active && !editingThis
          ? (e) => {
              if (e.button !== 0 || el.locked || e.shiftKey) {
                e.stopPropagation();
                blurFields();
                ctx.select(el.id, e.shiftKey);
                return;
              }
              ctx.select(el.id);
              ctx.startDrag(e, el, 'move');
            }
          : undefined
      }
      onDoubleClick={ctx.active && el.type === 'text' && !el.locked ? () => ctx.startEdit(el.id) : undefined}
    >
      <Body el={el} update={update} />
    </div>
  );
}

/** The height of a free element in mm; text boxes grow with their text, so they are measured. */
export function measuredHeight(el: FreeEl): number {
  if (el.type !== 'text') return el.h;
  const node = document.querySelector<HTMLElement>(`.canvas [data-free="${el.id}"]`);
  return node ? node.offsetHeight * PX_TO_MM : el.h;
}

const HANDLES: Record<FreeEl['type'], Handle[]> = {
  text: ['w', 'e'],
  line: ['w', 'e'],
  rect: ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w'],
  ellipse: ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w'],
  image: ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w'],
  icon: ['nw', 'ne', 'se', 'sw'],
};

function Selection({ el }: { el: FreeEl }) {
  const ctx = useContext(CanvasContext);
  const [h, setH] = useState(el.h);
  useLayoutEffect(() => {
    const m = measuredHeight(el);
    if (Math.abs(m - h) > 0.05) setH(m);
  });
  const height = el.type === 'text' ? h : el.h;
  return (
    <div
      className={`selection ${el.locked ? 'locked' : ''} ${ctx.editing === el.id ? 'editing' : ''}`}
      style={{ ...boxCss(el, `${height}mm`), ...(el.type === 'line' ? { top: `${el.y}mm` } : {}) }}
      onPointerDown={(e) => {
        if (e.button !== 0 || el.locked) {
          e.stopPropagation();
          blurFields();
          return;
        }
        ctx.startDrag(e, el, 'move');
      }}
      onDoubleClick={el.type === 'text' && !el.locked ? () => ctx.startEdit(el.id) : undefined}
    >
      {!el.locked &&
        ctx.editing !== el.id && (
          <>
            {HANDLES[el.type].map((hd) => (
              <span key={hd} className={`handle h-${hd}`} onPointerDown={(e) => ctx.startDrag(e, el, hd)} />
            ))}
            <span className="handle h-rot" title="Rotate" onPointerDown={(e) => ctx.startDrag(e, el, 'rot')}>
              ⟳
            </span>
          </>
        )}
      {el.locked && <span className="lock-badge">Locked</span>}
    </div>
  );
}

/** Free elements behind the CV's text, or in front of it (with the selection box and snapping guides). */
export function FreeLayer({ els, behind = false, update }: { els: FreeEl[]; behind?: boolean; update: Update }) {
  const ctx = useContext(CanvasContext);
  const mine = els.filter((e) => !!e.behind === behind);
  const selected = !behind && ctx.active ? els.find((e) => e.id === ctx.selected) : undefined;
  if (!mine.length && !selected && !(ctx.guides && !behind)) return null;
  return (
    <div className={`free-layer ${behind ? 'behind' : 'front'}`}>
      {mine.map((el) => (
        <Item key={el.id} el={el} update={update} />
      ))}
      {selected && <Selection el={selected} />}
      {!behind && ctx.guides && (
        <>
          {ctx.guides.x.map((x, i) => (
            <div key={`x${i}`} className="guide guide-v ui-only" style={{ left: `${x}mm` }} />
          ))}
          {ctx.guides.y.map((y, i) => (
            <div key={`y${i}`} className="guide guide-h ui-only" style={{ top: `${y}mm` }} />
          ))}
        </>
      )}
    </div>
  );
}

// --- dragging ---------------------------------------------------------------------------------------------------------

const rotate = (x: number, y: number, deg: number): [number, number] => {
  const r = (deg * Math.PI) / 180;
  return [x * Math.cos(r) - y * Math.sin(r), x * Math.sin(r) + y * Math.cos(r)];
};

/** Lines things snap to: page edges and centre, the sidebar's edge, and other free elements. */
function snapTargets(skip: string, d: CvData): Guides {
  const x = [0, PAGE_W / 2, PAGE_W];
  const y = [0, PAGE_H / 2, PAGE_H];
  const page = document.querySelector<HTMLElement>('.canvas .page');
  const side = page?.querySelector<HTMLElement>('.side');
  if (page && side) {
    const pr = page.getBoundingClientRect();
    const sr = side.getBoundingClientRect();
    const mm = PAGE_W / pr.width;
    x.push((sr.left - pr.left) * mm, (sr.right - pr.left) * mm);
  }
  for (const f of d.design.free) {
    if (f.id === skip || f.rot) continue;
    const h = measuredHeight(f);
    x.push(f.x, f.x + f.w / 2, f.x + f.w);
    y.push(f.y, f.y + h / 2, f.y + h);
  }
  return { x, y };
}

/** Moves `pos` (the element's start, middle and end along one axis) onto the closest target within reach. */
function snap(edges: number[], targets: number[], reach: number): { shift: number; at: number[] } {
  let best: { shift: number; at: number[] } = { shift: 0, at: [] };
  let bestDist = reach;
  for (const e of edges)
    for (const t of targets) {
      const dist = Math.abs(t - e);
      if (dist < bestDist - 0.01) {
        bestDist = dist;
        best = { shift: t - e, at: [t] };
      } else if (Math.abs(dist - bestDist) < 0.01 && best.at.length && !best.at.includes(t) && Math.abs(t - e - best.shift) < 0.01) {
        best.at.push(t);
      }
    }
  return best;
}

export function useFreeDrag(
  get: () => CvData | null,
  update: Update,
  checkpoint: () => void,
  setGuides: (g: Guides | null) => void,
) {
  return (e: RPointerEvent, el: FreeEl, mode: Handle) => {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    blurFields();
    const page = document.querySelector<HTMLElement>('.canvas .page');
    const data = get();
    if (!page || !data) return;
    const rect = page.getBoundingClientRect();
    const mm = PAGE_W / rect.width; // mm per screen pixel, whatever the zoom
    const start = { ...el, h: measuredHeight(el) };
    const sx = e.clientX;
    const sy = e.clientY;
    const targets = snapTargets(el.id, data);
    const reach = 6 * mm;
    let moved = false;

    const onMove = (ev: PointerEvent) => {
      let dx = (ev.clientX - sx) * mm;
      let dy = (ev.clientY - sy) * mm;
      if (!moved) {
        if (Math.hypot(dx, dy) < 0.6 * mm * 3) return;
        moved = true;
        checkpoint();
      }
      const next: Partial<FreeEl> = {};
      let guides: Guides | null = null;
      if (mode === 'move') {
        if (ev.shiftKey) {
          if (Math.abs(dx) > Math.abs(dy)) dy = 0;
          else dx = 0;
        }
        let nx = start.x + dx;
        let ny = start.y + dy;
        if (!ev.altKey && !start.rot) {
          const sxs = snap([nx, nx + start.w / 2, nx + start.w], targets.x, reach);
          const sys = snap([ny, ny + start.h / 2, ny + start.h], targets.y, reach);
          nx += sxs.shift;
          ny += sys.shift;
          guides = { x: sxs.at, y: sys.at };
        }
        next.x = round(nx);
        next.y = round(ny);
      } else if (mode === 'rot') {
        const cx = rect.left + (start.x + start.w / 2) / mm;
        const cy = rect.top + (start.y + start.h / 2) / mm;
        let deg = (Math.atan2(ev.clientY - cy, ev.clientX - cx) * 180) / Math.PI + 90;
        if (ev.shiftKey) deg = Math.round(deg / 15) * 15;
        else {
          const near = Math.round(deg / 45) * 45;
          if (Math.abs(deg - near) < 3) deg = near;
        }
        deg = ((((deg + 180) % 360) + 360) % 360) - 180;
        next.rot = Math.round(deg * 10) / 10;
      } else {
        const sxDir = mode.includes('e') ? 1 : mode.includes('w') ? -1 : 0;
        const syDir = mode.includes('s') ? 1 : mode === 'n' || mode.startsWith('n') ? -1 : 0;
        const [lx, ly] = rotate(dx, dy, -start.rot);
        let w = Math.max(2, start.w + sxDir * lx);
        let h = Math.max(el.type === 'line' ? start.h : 2, start.h + syDir * ly);
        const corner = sxDir !== 0 && syDir !== 0;
        if (corner && (el.type === 'image' || el.type === 'icon' || ev.shiftKey)) {
          const k = Math.max(w / start.w, h / start.h);
          w = start.w * k;
          h = start.h * k;
        }
        const [ox, oy] = rotate((sxDir * (w - start.w)) / 2, (syDir * (h - start.h)) / 2, start.rot);
        const cx = start.x + start.w / 2 + ox;
        const cy = start.y + start.h / 2 + oy;
        next.w = round(w);
        next.x = round(cx - w / 2);
        if (el.type !== 'text' && el.type !== 'line') {
          next.h = round(h);
          next.y = round(cy - h / 2);
        }
      }
      setGuides(guides && (guides.x.length || guides.y.length) ? guides : null);
      update((d) => {
        const f = d.design.free.find((x) => x.id === el.id);
        if (f) Object.assign(f, next);
      });
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      setGuides(null);
      if (moved) checkpoint();
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  };
}

const round = (n: number) => Math.round(n * 100) / 100;

/** Ends typing in a CV field or toolbar input, so keyboard shortcuts act on the selected element. */
export function blurFields() {
  const a = document.activeElement as HTMLElement | null;
  if (a && (a.isContentEditable || a.tagName === 'INPUT' || a.tagName === 'TEXTAREA')) a.blur();
}

// --- creating elements ------------------------------------------------------------------------------------------------

export function newFree(type: FreeEl['type'], extra: Partial<FreeEl> = {}): FreeEl {
  const size: Record<FreeEl['type'], [number, number]> = {
    text: [80, 10], rect: [40, 25], ellipse: [25, 25], line: [60, 2], image: [40, 40], icon: [8, 8],
  };
  const [w, h] = [extra.w ?? size[type][0], extra.h ?? size[type][1]];
  return {
    id: uid('el'),
    type,
    x: round((PAGE_W - w) / 2),
    y: round(PAGE_H / 3 - h / 2),
    w,
    h,
    rot: 0,
    ...extra,
  };
}
