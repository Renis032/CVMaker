// The text behind a selected element, for features that read or replace it (Rewrite with Claude). A field with
// this version's own wording is read and written there, so the profile keeps its text.
import type { CvData, Variant } from './types';
import type { Sel } from './Toolbar';

export interface TextField {
  label: string;
  get: () => string;
  set: (d: CvData, value: string) => void;
}

export function textField(data: CvData, variant: Variant, sel: Sel): TextField | null {
  if (!sel) return null;
  const vi = data.variants.findIndex((v) => v.id === variant.id);
  if (sel.type === 'free') {
    const el = data.design.free.find((f) => f.id === sel.id);
    if (!el || el.type !== 'text') return null;
    return { label: 'text box', get: () => el.text ?? '', set: (d, v) => void (d.design.free.find((f) => f.id === sel.id)!.text = v) };
  }
  const c = data.content;
  const owned = (key: string, label: string, shared: () => string, setShared: (d: CvData, v: string) => void): TextField => ({
    label,
    get: () => (key in variant.overrides ? variant.overrides[key] : shared()),
    set: (d, v) => (key in d.variants[vi].overrides ? void (d.variants[vi].overrides[key] = v) : setShared(d, v)),
  });
  if (sel.k === 'summary') return owned('summary', 'summary', () => c.summary, (d, v) => void (d.content.summary = v));
  if (sel.k === 'title') return owned('title', 'job title', () => c.title, (d, v) => void (d.content.title = v));
  if (sel.k === 'bullet' && sel.i) {
    for (const [list, kind] of [[c.experience, 'experience'], [c.projects, 'projects']] as const) {
      for (const [ii, item] of list.entries()) {
        const bi = item.bullets.findIndex((b) => b.id === sel.i);
        if (bi < 0) continue;
        return owned(`b:${sel.i}`, 'bullet', () => item.bullets[bi].text, (d, v) => void (d.content[kind][ii].bullets[bi].text = v));
      }
    }
  }
  if (sel.k === 'skillValue' && sel.i) {
    const gi = c.skills.findIndex((g) => g.id === sel.i);
    if (gi >= 0) return owned(`skill:${sel.i}`, 'skill list', () => c.skills[gi].value, (d, v) => void (d.content.skills[gi].value = v));
  }
  return null;
}
