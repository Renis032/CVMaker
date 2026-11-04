import { DndContext, PointerSensor, closestCenter, useSensor, useSensors, type DragEndEvent } from '@dnd-kit/core';
import { SortableContext, arrayMove, useSortable, verticalListSortingStrategy } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import type { CvData, Variant } from './types';
import { columns, newVariant, ordered, sectionTitle } from './model';
import type { Update } from './CvPage';

interface Row { id: string; label: string }

function SortRow({ row, hidden, onToggle }: { row: Row; hidden: boolean; onToggle: () => void }) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: row.id });
  return (
    <li
      ref={setNodeRef}
      className={`sort-row ${hidden ? 'is-hidden' : ''} ${isDragging ? 'dragging' : ''}`}
      style={{ transform: CSS.Transform.toString(transform), transition }}
    >
      <span className="grip" {...attributes} {...listeners} title="Drag to reorder">
        ⠿
      </span>
      <span className="sort-label">{row.label}</span>
      <button className="eye" onClick={onToggle} title={hidden ? 'Show in this version' : 'Hide in this version'}>
        {hidden ? 'Hidden' : 'Shown'}
      </button>
    </li>
  );
}

function SortableList({ rows, hidden, onReorder, onToggle }: {
  rows: Row[];
  hidden: Set<string>;
  onReorder: (ids: string[]) => void;
  onToggle: (id: string) => void;
}) {
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 3 } }));
  const ids = rows.map((r) => r.id);
  const onDragEnd = (e: DragEndEvent) => {
    if (!e.over || e.active.id === e.over.id) return;
    onReorder(arrayMove(ids, ids.indexOf(String(e.active.id)), ids.indexOf(String(e.over.id))));
  };
  return (
    <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
      <SortableContext items={ids} strategy={verticalListSortingStrategy}>
        <ul className="sort-list">
          {rows.map((r) => (
            <SortRow key={r.id} row={r} hidden={hidden.has(r.id)} onToggle={() => onToggle(r.id)} />
          ))}
        </ul>
      </SortableContext>
    </DndContext>
  );
}

export function VersionsPanel({ data, variant, update, select }: {
  data: CvData;
  variant: Variant;
  update: Update;
  select: (id: string) => void;
}) {
  const vi = data.variants.findIndex((v) => v.id === variant.id);
  const setV = (fn: (v: Variant) => void) => update((d) => fn(d.variants[vi]));
  const hidden = new Set(variant.hidden);
  const toggle = (id: string) =>
    setV((v) => {
      v.hidden = v.hidden.includes(id) ? v.hidden.filter((x) => x !== id) : [...v.hidden, id];
    });
  const c = data.content;
  const tailored = data.mode === 'cv';
  const cols = columns(variant, data.design.layout.sidebar === 'none' ? { ...data.design.layout, sidebar: 'right' } : data.design.layout, c);

  return (
    <div className="panel">
      {tailored ? (
        <>
          <h3>Tailored CV</h3>
          <p className="hint">
            This CV was made for one job. Text you change here is saved to this CV only, not to your profile. Order and
            what is shown are this CV's own; the style is shared by all your CVs.
          </p>
        </>
      ) : (
        <>
      <h3>Versions</h3>
      <ul className="version-list">
        {data.variants.map((v) => (
          <li key={v.id} className={v.id === variant.id ? 'active' : ''} onClick={() => select(v.id)}>
            {v.name}
          </li>
        ))}
      </ul>
      <div className="row-buttons">
        <button
          onClick={() => {
            const nv = newVariant(variant);
            update((d) => void d.variants.splice(vi + 1, 0, nv));
            select(nv.id);
          }}
        >
          Duplicate
        </button>
        <button
          onClick={() => {
            const nv = newVariant();
            update((d) => void d.variants.push(nv));
            select(nv.id);
          }}
        >
          New
        </button>
        <button
          className="danger"
          disabled={data.variants.length < 2}
          onClick={() => {
            if (!confirm(`Delete version "${variant.name}"? Shared content is kept.`)) return;
            const next = data.variants[vi === 0 ? 1 : vi - 1].id;
            update((d) => void d.variants.splice(vi, 1));
            select(next);
          }}
        >
          Delete
        </button>
      </div>
      <p className="hint">The text is your CVMaker profile, shared by every version. Each version picks what to show and can keep its own wording.</p>
        </>
      )}

      <h3>{tailored ? 'This CV' : 'This version'}</h3>
      <label className="field">
        <span>Name</span>
        <input value={variant.name} onChange={(e) => setV((v) => void (v.name = e.target.value))} />
      </label>
      <label className="field">
        <span>PDF file name</span>
        <input value={variant.fileName} onChange={(e) => setV((v) => void (v.fileName = e.target.value))} />
      </label>
      <label className="field">
        <span>Location</span>
        <input value={variant.location} onChange={(e) => setV((v) => void (v.location = e.target.value))} />
      </label>
      {(['title', 'summary'] as const).map((key) => (
        <label className="check" key={key}>
          <input
            type="checkbox"
            checked={key in variant.overrides}
            onChange={(e) =>
              setV((v) => {
                if (e.target.checked) v.overrides[key] = c[key];
                else delete v.overrides[key];
              })
            }
          />
          Own {key === 'title' ? 'job title' : 'summary'} for this version
        </label>
      ))}
      {Object.keys(variant.overrides).length > 0 && (
        <p className="hint">
          {Object.keys(variant.overrides).length} field{Object.keys(variant.overrides).length > 1 ? 's' : ''} use this version's own wording
          (<span className="own-sample">underlined</span> on the page); your profile keeps its text.
        </p>
      )}

      <p className="hint">What this version shows, and in which order, is set in <strong>Content</strong> (Shown / Hidden, drag to reorder).</p>
    </div>
  );
}
