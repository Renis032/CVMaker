// A dropdown in the site's style (the browser's own <select> list can't be styled).
import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react';

export interface Option<T extends string> {
  value: T;
  label: string;
  style?: CSSProperties;
}

export function Dropdown<T extends string>({ value, options, onChange, title, className = '', render }: {
  value: T;
  options: Option<T>[];
  onChange: (v: T) => void;
  title?: string;
  className?: string;
  render?: (o: Option<T>) => ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const box = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const current = options.find((o) => o.value === value);

  useEffect(() => {
    if (!open) return;
    setActive(Math.max(0, options.findIndex((o) => o.value === value)));
    const close = (e: PointerEvent) => !box.current?.contains(e.target as Node) && setOpen(false);
    document.addEventListener('pointerdown', close);
    return () => document.removeEventListener('pointerdown', close);
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    list.current?.querySelector<HTMLElement>(`[data-ix="${active}"]`)?.scrollIntoView({ block: 'nearest' });
  }, [active, open]);

  const pick = (o: Option<T>) => {
    onChange(o.value);
    setOpen(false);
  };
  const onKey = (e: React.KeyboardEvent) => {
    if (!open && (e.key === 'ArrowDown' || e.key === 'Enter' || e.key === ' ')) {
      e.preventDefault();
      setOpen(true);
    } else if (open && e.key === 'ArrowDown') {
      e.preventDefault();
      setActive((a) => Math.min(options.length - 1, a + 1));
    } else if (open && e.key === 'ArrowUp') {
      e.preventDefault();
      setActive((a) => Math.max(0, a - 1));
    } else if (open && e.key === 'Enter') {
      e.preventDefault();
      pick(options[active]);
    } else if (e.key === 'Escape') {
      setOpen(false);
    }
  };
  return (
    <div className={`dd ${open ? 'open' : ''} ${className}`} ref={box}>
      <button type="button" className="dd-btn" title={title} aria-haspopup="listbox" aria-expanded={open} onClick={() => setOpen(!open)} onKeyDown={onKey}>
        <span className="dd-val" style={current?.style}>{current ? (render ? render(current) : current.label) : ''}</span>
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round"><path d="m6 9 6 6 6-6" /></svg>
      </button>
      {open && (
        <div className="dd-list" role="listbox" ref={list}>
          {options.map((o, ix) => (
            <button
              type="button"
              key={o.value}
              data-ix={ix}
              role="option"
              aria-selected={o.value === value}
              className={`dd-opt ${o.value === value ? 'on' : ''} ${ix === active ? 'active' : ''}`}
              style={o.style}
              onMouseEnter={() => setActive(ix)}
              onClick={() => pick(o)}
            >
              <span>{render ? render(o) : o.label}</span>
              {o.value === value && <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round"><path d="M20 6 9 17l-5-5" /></svg>}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
