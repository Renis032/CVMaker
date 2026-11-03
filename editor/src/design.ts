// The design layer: what each element on the page is called, the CSS for element styles, templates, colour
// palettes, font sets and fonts.
import type { CvData, Design, ElemStyle, PageLayout, Style } from './types';

export const DEFAULT_LAYOUT: PageLayout = {
  sidebar: 'right',
  header: 'main',
  photo: 'side',
  headingStyle: 'underline',
  bullet: 'default',
  contactIcons: false,
  pageBg: '#ffffff',
};

export function withDefaults(d: Partial<Design> | undefined): Design {
  return { layout: { ...DEFAULT_LAYOUT, ...(d?.layout ?? {}) }, elements: { ...(d?.elements ?? {}) }, free: [...(d?.free ?? [])] };
}

// --- element kinds ------------------------------------------------------------------------------------------------

export type Category = 'text' | 'image' | 'box';

interface Kind {
  label: string;
  plural?: string; // set for kinds that repeat (styles can apply to all of them or to one)
  cat: Category;
}

export const KINDS: Record<string, Kind> = {
  page: { label: 'Page', cat: 'box' },
  header: { label: 'Header', cat: 'box' },
  mainCol: { label: 'Main column', cat: 'box' },
  sideCol: { label: 'Sidebar', cat: 'box' },
  section: { label: 'Section', plural: 'sections', cat: 'box' },
  heading: { label: 'Section heading', plural: 'section headings', cat: 'text' },
  sideHeading: { label: 'Sidebar heading', plural: 'sidebar headings', cat: 'text' },
  name: { label: 'Name', cat: 'text' },
  title: { label: 'Job title', cat: 'text' },
  photo: { label: 'Photo', cat: 'image' },
  summary: { label: 'Summary', cat: 'text' },
  job: { label: 'Job', plural: 'jobs', cat: 'box' },
  jobRole: { label: 'Role', plural: 'roles', cat: 'text' },
  jobCompany: { label: 'Company', plural: 'companies', cat: 'text' },
  jobDates: { label: 'Dates', plural: 'dates', cat: 'text' },
  jobLogo: { label: 'Company logo', plural: 'logos', cat: 'image' },
  bullet: { label: 'Bullet', plural: 'bullets', cat: 'text' },
  project: { label: 'Project', plural: 'projects', cat: 'box' },
  projectTitle: { label: 'Project name', plural: 'project names', cat: 'text' },
  projectTech: { label: 'Project tech', plural: 'project tech lines', cat: 'text' },
  projectsLink: { label: 'Projects link', cat: 'text' },
  contact: { label: 'Contact line', plural: 'contact lines', cat: 'text' },
  skill: { label: 'Skill group', plural: 'skill groups', cat: 'box' },
  skillLabel: { label: 'Skill label', plural: 'skill labels', cat: 'text' },
  skillValue: { label: 'Skill list', plural: 'skill lists', cat: 'text' },
  listItem: { label: 'List item', plural: 'list items', cat: 'text' },
  edu: { label: 'Education entry', plural: 'education entries', cat: 'box' },
  eduDegree: { label: 'Degree', plural: 'degrees', cat: 'text' },
  eduSchool: { label: 'School', plural: 'schools', cat: 'text' },
};

const HEADINGS = new Set(['heading', 'sideHeading']);
/** Kinds drawn inline in the text flow: they need inline-block to take a width or height. */
const INLINE = new Set(['bullet', 'listItem', 'contact', 'projectTitle', 'projectTech', 'projectsLink']);

export const styleKey = (k: string, i?: string) => (i ? `${k}#${i}` : k);

/** An element's style: its kind's style with its own on top. */
export function elemStyle(design: Design, k: string, i?: string): ElemStyle {
  return { ...(design.elements[k] ?? {}), ...(i ? design.elements[`${k}#${i}`] ?? {} : {}) };
}

function decls(k: string, s: ElemStyle): string {
  const out: string[] = [];
  const add = (prop: string, v: string | number | undefined) => v !== undefined && v !== '' && out.push(`${prop}:${v}`);
  add('font-family', s.font);
  if (s.size !== undefined) add('font-size', `calc(var(--fs) * ${s.size})`);
  add('color', s.color);
  add('background-color', s.bg);
  if (s.bold !== undefined) add('font-weight', s.bold ? 700 : 400);
  if (s.italic !== undefined) add('font-style', s.italic ? 'italic' : 'normal');
  if (s.underline !== undefined) add('text-decoration', s.underline ? 'underline' : 'none');
  if (s.upper !== undefined) add('text-transform', s.upper ? 'uppercase' : 'none');
  add('text-align', s.align);
  if (s.letterSpacing !== undefined) add('letter-spacing', `${s.letterSpacing}em`);
  add('line-height', s.lineHeight);
  add('opacity', s.opacity);
  if (s.padding !== undefined) add('padding', `${s.padding}mm`);
  if (s.radius !== undefined) add('border-radius', `${s.radius}mm`);
  if (HEADINGS.has(k)) {
    if (s.borderWidth !== undefined) out.push(`border-bottom-width:${s.borderWidth}mm`, 'border-bottom-style:solid');
    add('border-bottom-color', s.borderColor);
  } else if (s.borderWidth) {
    add('border', `${s.borderWidth}mm solid ${s.borderColor || 'currentColor'}`);
  }
  if (s.spaceAbove !== undefined) add('margin-top', `${s.spaceAbove}mm`);
  if (s.spaceBelow !== undefined) add('margin-bottom', `${s.spaceBelow}mm`);
  if (s.width !== undefined || s.height !== undefined) {
    if (INLINE.has(k)) out.push('display:inline-block', 'vertical-align:top');
    if (s.width !== undefined) out.push(`width:${s.width}mm`, 'max-width:none', 'flex:none', 'box-sizing:border-box');
    if (s.height !== undefined) out.push(`min-height:${s.height}mm`);
  }
  if (s.imgSize !== undefined) out.push(`width:${s.imgSize}mm`, `height:${s.imgSize}mm`);
  if (s.round !== undefined) add('border-radius', `${s.round}%`);
  return out.join(';');
}

/** The stylesheet for every element style: kind rules first, then rules for single elements (more specific). */
export function elementsCss(elements: Record<string, ElemStyle>): string {
  const rules: string[] = [];
  const entries = Object.entries(elements).sort(([a], [b]) => Number(a.includes('#')) - Number(b.includes('#')));
  for (const [key, s] of entries) {
    const [k, i] = key.split('#');
    const body = decls(k, s);
    if (!body) continue;
    const sel = `.page.page.page [data-k=${JSON.stringify(k)}]${i ? `[data-i=${JSON.stringify(i)}]` : ''}`;
    // The page itself carries data-k="page".
    rules.push(`${k === 'page' ? '.page.page.page[data-k="page"]' : sel}{${body}}`);
  }
  return rules.join('\n');
}

// --- colours --------------------------------------------------------------------------------------------------------

export const DEFAULT_COLORS = [
  '#000000', '#3f3f46', '#71717a', '#a1a1aa', '#d4d4d8', '#f4f4f5', '#ffffff',
  '#7f1d1d', '#dc2626', '#f97316', '#f59e0b', '#facc15', '#84cc16', '#16a34a',
  '#0d9488', '#06b6d4', '#0ea5e9', '#2563eb', '#1e3a8a', '#1a3a5c', '#22405c',
  '#4f46e5', '#7c3aed', '#c026d3', '#db2777', '#fecaca', '#bfdbfe', '#d1fae5',
];

/** Colours already used in this CV, most recent kinds first ("Document colours" in the colour picker). */
export function documentColors(d: CvData): string[] {
  const s = d.style;
  const found = [s.nameColor, s.accent, s.heading, s.text, s.muted, s.sidebarBg, s.sidebarText, s.sidebarLabel, d.design.layout.pageBg];
  for (const e of Object.values(d.design.elements)) found.push(e.color ?? '', e.bg ?? '', e.borderColor ?? '');
  for (const f of d.design.free) found.push(f.fill ?? '', f.stroke ?? '', f.style?.color ?? '');
  return [...new Set(found.filter((c) => /^#[0-9a-f]{6}$/i.test(c)).map((c) => c.toLowerCase()))];
}

export function toHex(color: string): string {
  const m = color.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
  if (!m) return color.startsWith('#') ? color : '#000000';
  return `#${[m[1], m[2], m[3]].map((n) => Number(n).toString(16).padStart(2, '0')).join('')}`;
}

// --- fonts ------------------------------------------------------------------------------------------------------------

/** Fonts offered in the editor. All but the first come from Google Fonts. */
export const FONTS: [string, string][] = [
  ['Helvetica / Arial', "Helvetica, Arial, 'Liberation Sans', sans-serif"],
  ['Inter', "'Inter', sans-serif"],
  ['Roboto', "'Roboto', sans-serif"],
  ['Open Sans', "'Open Sans', sans-serif"],
  ['Lato', "'Lato', sans-serif"],
  ['Source Sans 3', "'Source Sans 3', sans-serif"],
  ['Montserrat', "'Montserrat', sans-serif"],
  ['Poppins', "'Poppins', sans-serif"],
  ['Raleway', "'Raleway', sans-serif"],
  ['Nunito', "'Nunito', sans-serif"],
  ['Work Sans', "'Work Sans', sans-serif"],
  ['DM Sans', "'DM Sans', sans-serif"],
  ['IBM Plex Sans', "'IBM Plex Sans', sans-serif"],
  ['Fira Sans', "'Fira Sans', sans-serif"],
  ['Rubik', "'Rubik', sans-serif"],
  ['Josefin Sans', "'Josefin Sans', sans-serif"],
  ['Oswald', "'Oswald', sans-serif"],
  ['Merriweather', "'Merriweather', serif"],
  ['Playfair Display', "'Playfair Display', serif"],
  ['Lora', "'Lora', serif"],
  ['PT Serif', "'PT Serif', serif"],
  ['EB Garamond', "'EB Garamond', serif"],
  ['Libre Baskerville', "'Libre Baskerville', serif"],
  ['Crimson Text', "'Crimson Text', serif"],
];

export function fontLabel(value: string | undefined): string {
  if (!value) return '';
  const found = FONTS.find(([, v]) => v === value);
  return found ? found[0] : value.split(',')[0].replace(/['"]/g, '');
}

/** The Google Fonts stylesheet for every font the editor offers. */
export function allFontsUrl(): string {
  const families = FONTS.slice(1).map(([label]) => `family=${label.replace(/ /g, '+')}:wght@400;700`);
  return `https://fonts.googleapis.com/css2?${families.join('&')}&display=swap`;
}

// --- templates, palettes, font sets ---------------------------------------------------------------------------------

type ColorSet = Pick<Style, 'nameColor' | 'accent' | 'heading' | 'text' | 'muted' | 'sidebarBg' | 'sidebarText' | 'sidebarLabel'>;

export interface Template {
  name: string;
  colors: ColorSet;
  font: string;
  headFont?: string;
  layout: Partial<PageLayout>;
  elements?: Record<string, ElemStyle>;
}

const NAVY: ColorSet = {
  nameColor: '#1a3a5c', accent: '#008cff', heading: '#1a3a5c', text: '#222222', muted: '#555555',
  sidebarBg: '#22405c', sidebarText: '#ffffff', sidebarLabel: '#a8c5e2',
};

export const TEMPLATES: Template[] = [
  { name: 'Navy sidebar', colors: NAVY, font: FONTS[0][1], layout: {} },
  {
    name: 'Teal, sidebar left',
    colors: { nameColor: '#134e4a', accent: '#0d9488', heading: '#134e4a', text: '#1f2937', muted: '#64748b', sidebarBg: '#134e4a', sidebarText: '#f0fdfa', sidebarLabel: '#99f6e4' },
    font: "'Lato', sans-serif",
    layout: { sidebar: 'left', headingStyle: 'side', contactIcons: true },
  },
  {
    name: 'Banner header',
    colors: { nameColor: '#ffffff', accent: '#2563eb', heading: '#1f2937', text: '#1f2937', muted: '#6b7280', sidebarBg: '#f3f4f6', sidebarText: '#1f2937', sidebarLabel: '#2563eb' },
    font: "'Inter', sans-serif",
    layout: { header: 'full', photo: 'header', headingStyle: 'bar', contactIcons: true },
    elements: { header: { bg: '#1f2937', padding: 6 }, title: { color: '#93c5fd' } },
  },
  {
    name: 'Minimal, one column',
    colors: { nameColor: '#111111', accent: '#111111', heading: '#111111', text: '#262626', muted: '#737373', sidebarBg: '#ffffff', sidebarText: '#262626', sidebarLabel: '#111111' },
    font: "'Inter', sans-serif",
    layout: { sidebar: 'none', photo: 'none', headingStyle: 'plain', bullet: '–' },
    elements: { heading: { letterSpacing: 0.12, size: 1.1 } },
  },
  {
    name: 'Elegant serif',
    colors: { nameColor: '#7f1d1d', accent: '#9f1239', heading: '#7f1d1d', text: '#27272a', muted: '#71717a', sidebarBg: '#faf5f0', sidebarText: '#3f3f46', sidebarLabel: '#9f1239' },
    font: "'Lora', serif",
    headFont: "'Playfair Display', serif",
    layout: { sidebar: 'left', headingStyle: 'plain', bullet: '•' },
  },
  {
    name: 'Charcoal & amber',
    colors: { nameColor: '#18181b', accent: '#d97706', heading: '#18181b', text: '#27272a', muted: '#71717a', sidebarBg: '#27272a', sidebarText: '#fafafa', sidebarLabel: '#fbbf24' },
    font: "'Montserrat', sans-serif",
    layout: { headingStyle: 'side', bullet: '▸', contactIcons: true },
  },
];

export const PALETTES: [string, ColorSet][] = [
  ['Navy', NAVY],
  ['Forest', { nameColor: '#14532d', accent: '#16a34a', heading: '#14532d', text: '#1f2937', muted: '#6b7280', sidebarBg: '#14532d', sidebarText: '#f0fdf4', sidebarLabel: '#86efac' }],
  ['Plum', { nameColor: '#4c1d95', accent: '#7c3aed', heading: '#4c1d95', text: '#1f2937', muted: '#6b7280', sidebarBg: '#3b0764', sidebarText: '#faf5ff', sidebarLabel: '#d8b4fe' }],
  ['Burgundy', { nameColor: '#7f1d1d', accent: '#be123c', heading: '#7f1d1d', text: '#27272a', muted: '#71717a', sidebarBg: '#4c0519', sidebarText: '#fff1f2', sidebarLabel: '#fda4af' }],
  ['Slate', { nameColor: '#0f172a', accent: '#0284c7', heading: '#0f172a', text: '#1e293b', muted: '#64748b', sidebarBg: '#e2e8f0', sidebarText: '#0f172a', sidebarLabel: '#0369a1' }],
  ['Sand', { nameColor: '#422006', accent: '#b45309', heading: '#422006', text: '#292524', muted: '#78716c', sidebarBg: '#f5f0e6', sidebarText: '#292524', sidebarLabel: '#b45309' }],
  ['Teal', TEMPLATES[1].colors],
  ['Black & white', { nameColor: '#000000', accent: '#000000', heading: '#000000', text: '#1a1a1a', muted: '#666666', sidebarBg: '#111111', sidebarText: '#ffffff', sidebarLabel: '#cccccc' }],
];

/** Heading font + body font pairs. */
export const FONT_SETS: [string, string][] = [
  ["'Montserrat', sans-serif", "'Open Sans', sans-serif"],
  ["'Playfair Display', serif", "'Lato', sans-serif"],
  ["'Raleway', sans-serif", "'Roboto', sans-serif"],
  ["'Merriweather', serif", "'Source Sans 3', sans-serif"],
  ["'Poppins', sans-serif", "'Inter', sans-serif"],
  ["'Oswald', sans-serif", "'Lato', sans-serif"],
  ["'EB Garamond', serif", "'EB Garamond', serif"],
  ["'DM Sans', sans-serif", "'DM Sans', sans-serif"],
];

/** Elements that use the heading font of a font set. */
export const HEADING_KINDS = ['name', 'heading', 'sideHeading', 'jobRole', 'projectTitle'];

export function applyFontSet(d: CvData, head: string, body: string) {
  d.style.font = body;
  for (const k of HEADING_KINDS) {
    const s = { ...(d.design.elements[k] ?? {}) };
    if (head === body) delete s.font;
    else s.font = head;
    d.design.elements[k] = s;
  }
}

export function applyTemplate(d: CvData, t: Template) {
  Object.assign(d.style, t.colors, { font: t.font });
  d.design.layout = { ...DEFAULT_LAYOUT, ...t.layout };
  d.design.elements = structuredClone(t.elements ?? {});
  if (t.headFont) applyFontSet(d, t.headFont, t.font);
}
