/** Text fields marked Rich support **bold** markup. */
export type Rich = string;

/** A bullet. Ids let a version hide one bullet or keep its own wording for it. */
export interface Bullet {
  id: string;
  text: Rich;
}

export interface Experience {
  id: string;
  role: string;
  company: string;
  logo: string;
  dates: string;
  bullets: Bullet[];
}

export interface Project {
  id: string;
  title: string;
  tech: string;
  bullets: Bullet[];
}

export interface SkillGroup {
  id: string;
  label: string;
  value: string;
}

export interface Education {
  id: string;
  degree: string;
  school: string;
}

/** An entry of a custom section: a certificate, award, volunteer role, paper... */
export interface CustomEntry {
  id: string;
  title: string;
  subtitle: string;
  dates: string;
  bullets: Bullet[];
}

/** A section the fixed ones don't cover (Certifications, Awards, Volunteering...), with its own heading. */
export interface CustomSection {
  id: string;
  title: string;
  entries: CustomEntry[];
}

export type MainSection = 'summary' | 'experience' | 'projects';
export type SideSection = 'contact' | 'skills' | 'softSkills' | 'languages' | 'interests' | 'education';
export type BuiltinSection = MainSection | SideSection;
/** Built-in sections by name; custom sections as "x:<id>". */
export type SectionKey = BuiltinSection | `x:${string}`;

export interface Content {
  name: string;
  title: string;
  photo: string;
  summary: Rich;
  contact: string[];
  headings: Record<BuiltinSection, string>;
  projectsLink: string;
  experience: Experience[];
  projects: Project[];
  skills: SkillGroup[];
  softSkills: string[];
  languages: string[];
  interests: string[];
  education: Education[];
  custom: CustomSection[];
}

/** One CV version: shares all content, overrides order/visibility and a few fields. */
export interface Variant {
  id: string;
  name: string;
  fileName: string;
  location: string;
  /** Wording this version keeps for itself instead of the shared (profile) text. Keys: "title", "summary",
   *  "b:<bulletId>", "skill:<groupId>", "tech:<projectId>", "degree:<educationId>". */
  overrides: Record<string, string>;
  /** Sections in the main column and in the sidebar, top to bottom (any section can go in either column). */
  mainOrder: SectionKey[];
  sideOrder: SectionKey[];
  projectOrder: string[];
  /** Section keys and item ids (jobs, projects, bullets, skill groups) hidden in this version. */
  hidden: string[];
}

export interface Style {
  font: string;
  fontSize: number; // pt
  lineHeight: number;
  sectionGap: number; // mm
  itemGap: number; // mm
  sidebarWidth: number; // %
  photoSize: number; // mm
  nameColor: string;
  accent: string;
  heading: string;
  text: string;
  muted: string;
  sidebarBg: string;
  sidebarText: string;
  sidebarLabel: string;
}

/** "profile": the CVMaker profile with your versions. "cv": one CV tailored for a job. "print": a PDF render. */
export type Mode = 'profile' | 'cv' | 'print';

export interface CvData {
  mode: Mode;
  /** Shown in the top bar, and where "← CVMaker" goes back to. */
  label?: string;
  back?: string;
  /** Revision of the content when loaded; the server refuses a save over changes made elsewhere. */
  rev?: string;
  content: Content;
  variants: Variant[];
  style: Style;
  /** A tailored CV's own text size (fitted to one page); replaces style.fontSize. */
  fontPt?: number | null;
  /** Page layout, styles of single elements and the free elements (text boxes, shapes, icons, images). */
  design: Design;
  /** A tailored CV's job: what the posting asks for (key skills and tools). */
  job?: { title: string; company: string; url: string; keywords: string[] };
}

/** How one kind of element (all job titles) or one element (this job title) looks. Unset = the template's look. */
export interface ElemStyle {
  font?: string;
  /** Text size as a multiple of the body text size, so fitting a CV to one page still scales it. */
  size?: number;
  color?: string;
  bg?: string;
  bold?: boolean;
  italic?: boolean;
  underline?: boolean;
  upper?: boolean;
  align?: 'left' | 'center' | 'right' | 'justify';
  letterSpacing?: number; // em
  lineHeight?: number;
  opacity?: number; // 0..1
  padding?: number; // mm
  radius?: number; // mm
  borderWidth?: number; // mm (the divider line for section headings)
  borderColor?: string;
  spaceAbove?: number; // mm
  spaceBelow?: number; // mm
  /** Box size in mm (unset = automatic). Height is a minimum: text never gets cut off. */
  width?: number;
  height?: number;
  imgSize?: number; // mm, photos and logos
  /** Moved from its place on the page, in mm (Canva-style free moving; the rest of the page doesn't shift). */
  dx?: number;
  dy?: number;
  round?: number; // %, photos and logos
}

export type FreeType = 'text' | 'rect' | 'ellipse' | 'line' | 'image' | 'icon';

/** An element placed freely on the page, like in Canva. Positions and sizes in mm from the page's top left. */
export interface FreeEl {
  id: string;
  type: FreeType;
  x: number;
  y: number;
  w: number;
  h: number;
  rot: number; // degrees
  /** Drawn behind the CV's text instead of on top of it. */
  behind?: boolean;
  locked?: boolean;
  text?: string;
  src?: string;
  icon?: string;
  fill?: string;
  stroke?: string;
  strokeWidth?: number; // mm
  dash?: 'solid' | 'dashed' | 'dotted';
  radius?: number; // mm
  opacity?: number;
  style?: ElemStyle;
}

export interface PageLayout {
  sidebar: 'right' | 'left' | 'none';
  /** The name and title at the top of the main column, or across the whole page. */
  header: 'main' | 'full';
  photo: 'side' | 'header' | 'none';
  /** Where the photo was before it was deleted (it comes back there). */
  photoWas?: 'side' | 'header';
  headingStyle: 'underline' | 'plain' | 'bar' | 'side';
  bullet: string; // 'default' or the character to use
  contactIcons: boolean;
  pageBg: string;
}

export interface Design {
  layout: PageLayout;
  /** Keys: a kind ("jobRole") for all of them, or "kind#id" ("jobRole#job-ab12") for one. */
  elements: Record<string, ElemStyle>;
  free: FreeEl[];
  /** Designs you saved as your own templates (Design tab). */
  saved?: import('./design').Template[];
}
