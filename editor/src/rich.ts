// Converts between the stored "**bold**" markup and the HTML shown in editable fields.

export function escapeHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

export function richToHtml(s: string): string {
  return escapeHtml(s).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/\n/g, '<br>');
}

interface Seg { text: string; bold: boolean }

function isBoldEl(el: HTMLElement, inherited: boolean): boolean {
  const tag = el.tagName;
  if (tag === 'B' || tag === 'STRONG') return true;
  const w = el.style.fontWeight;
  if (w === 'bold' || Number(w) >= 600) return true;
  if (w === 'normal' || (w && Number(w) < 600)) return false;
  return inherited;
}

function walk(node: Node, bold: boolean, out: Seg[]) {
  if (node.nodeType === Node.TEXT_NODE) {
    out.push({ text: node.textContent ?? '', bold });
    return;
  }
  if (!(node instanceof HTMLElement)) return;
  if (node.tagName === 'BR') {
    out.push({ text: '\n', bold: false });
    return;
  }
  const b = isBoldEl(node, bold);
  const block = node.tagName === 'DIV' || node.tagName === 'P';
  if (block && out.length) out.push({ text: '\n', bold: false });
  node.childNodes.forEach((c) => walk(c, b, out));
}

/** The field's text as stored: **bold** markup when rich; line breaks kept only when multiline. */
export function htmlToRich(el: HTMLElement, rich: boolean, multiline = false): string {
  const tidy = (t: string) =>
    multiline
      ? t.replace(/[^\S\n]+/g, ' ').replace(/ ?\n ?/g, '\n').replace(/\n+$/, '').trim()
      : t.replace(/\s+/g, ' ').trim();
  if (!rich) return tidy(el.textContent ?? '');
  const segs: Seg[] = [];
  el.childNodes.forEach((c) => walk(c, false, segs));
  const merged: Seg[] = [];
  for (const s of segs) {
    const text = s.text.replace(/[^\S\n]+/g, ' ');
    const last = merged[merged.length - 1];
    if (last && last.bold === s.bold) last.text += text;
    else merged.push({ text, bold: s.bold });
  }
  let out = '';
  for (const s of merged) {
    if (!s.bold || !s.text.trim()) {
      out += s.text;
      continue;
    }
    // Bold never spans a line break: bold each line on its own.
    out += s.text
      .split('\n')
      .map((line) => {
        if (!line.trim()) return line;
        const lead = line.match(/^\s*/)![0];
        const trail = line.match(/\s*$/)![0];
        return `${lead}**${line.trim()}**${trail}`;
      })
      .join('\n');
  }
  return tidy(out);
}
