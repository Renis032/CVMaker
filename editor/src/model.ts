import type { CSSProperties } from 'react';
import type { Content, MainSection, PageLayout, SectionKey, SideSection, Style, Variant } from './types';

export const MAIN_SECTIONS: MainSection[] = ['summary', 'experience', 'projects'];
export const SIDE_SECTIONS: SideSection[] = ['contact', 'skills', 'softSkills', 'languages', 'interests', 'education'];

export function uid(prefix: string): string {
  return `${prefix}-${Math.random().toString(36).slice(2, 8)}`;
}

/** Items in the order given by `order`; items missing from it are appended in their natural order. */
export function ordered<T extends { id: string }>(items: T[], order: string[]): T[] {
  const byId = new Map(items.map((i) => [i.id, i]));
  const out: T[] = [];
  for (const id of order) {
    const item = byId.get(id);
    if (item) {
      out.push(item);
      byId.delete(id);
    }
  }
  return out.concat([...byId.values()]);
}

export function orderedKeys<K extends string>(all: K[], order: K[]): K[] {
  const seen = order.filter((k) => all.includes(k));
  return [...new Set([...seen, ...all])];
}

export const customKey = (id: string): SectionKey => `x:${id}`;

/** The heading shown for a section: a custom section's own title, else the (renamable) built-in heading. */
export function sectionTitle(c: Content, key: SectionKey): string {
  if (key.startsWith('x:')) return c.custom.find((s) => customKey(s.id) === key)?.title || 'Section';
  return c.headings[key as keyof Content['headings']] ?? key;
}

/** The sections of each column, top to bottom. Any section can sit in either column; a section in neither goes to
 *  its usual column (custom sections: the main column). Without a sidebar everything is in the main column. */
export function columns(v: Variant, layout?: PageLayout, content?: Pick<Content, 'custom'>): { main: SectionKey[]; side: SectionKey[] } {
  const custom = (content?.custom ?? []).map((s) => customKey(s.id));
  const all: SectionKey[] = [...MAIN_SECTIONS, ...SIDE_SECTIONS, ...custom];
  const seen = new Set<SectionKey>();
  const take = (keys: SectionKey[]) => keys.filter((k) => all.includes(k) && !seen.has(k) && seen.add(k));
  const main = take(v.mainOrder ?? []);
  const side = take(v.sideOrder ?? []);
  for (const k of [...MAIN_SECTIONS, ...custom]) if (!seen.has(k)) main.push(k);
  for (const k of SIDE_SECTIONS) if (!seen.has(k)) side.push(k);
  return layout?.sidebar === 'none' ? { main: [...main, ...side], side: [] } : { main, side };
}

export function newVariant(from?: Variant): Variant {
  const base: Variant = from
    ? structuredClone(from)
    : {
        id: '',
        name: 'New version',
        fileName: 'CV',
        location: '',
        overrides: {},
        mainOrder: [...MAIN_SECTIONS],
        sideOrder: [...SIDE_SECTIONS],
        projectOrder: [],
        hidden: [],
      };
  base.id = uid('v');
  if (from) {
    base.name = `${from.name} (copy)`;
    base.fileName = `${from.fileName}_copy`;
  }
  return base;
}

export function styleVars(s: Style, layout?: PageLayout): CSSProperties {
  return {
    '--page-bg': layout?.pageBg || '#ffffff',
    '--bullet': layout && layout.bullet !== 'default' ? JSON.stringify(layout.bullet === 'none' ? '' : layout.bullet) : undefined,
    '--font': s.font,
    '--fs': `${s.fontSize}pt`,
    '--lh': String(s.lineHeight),
    '--gap': `${s.sectionGap}mm`,
    '--item-gap': `${s.itemGap}mm`,
    '--side-w': `${s.sidebarWidth}%`,
    '--photo': `${s.photoSize}mm`,
    '--name': s.nameColor,
    '--accent': s.accent,
    '--heading': s.heading,
    '--text': s.text,
    '--muted': s.muted,
    '--side-bg': s.sidebarBg,
    '--side-text': s.sidebarText,
    '--side-label': s.sidebarLabel,
  } as CSSProperties;
}

/** Where the app's API lives (the editor is served under /editor/ by CVMaker). */
export const API = `${import.meta.env.BASE_URL}api/`;

/** The document being edited: "?cv=12" is a tailored CV, otherwise the profile. */
export const docQuery = (() => {
  const cv = new URLSearchParams(location.search).get('cv');
  return cv ? `?cv=${encodeURIComponent(cv)}` : '';
})();
