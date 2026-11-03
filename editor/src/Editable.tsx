import { createContext, useContext, useLayoutEffect, useRef, type ElementType, type KeyboardEvent } from 'react';
import { escapeHtml, htmlToRich, richToHtml } from './rich';

export interface EditCtx {
  editable: boolean;
  /** Ask the page to focus the field with this id after the next render. */
  focus: (id: string) => void;
}

export const EditContext = createContext<EditCtx>({ editable: false, focus: () => {} });

interface Props {
  id: string;
  value: string;
  onChange: (v: string) => void;
  rich?: boolean;
  as?: ElementType;
  className?: string;
  placeholder?: string;
  /** Enter pressed: the field's text is committed first. */
  onEnter?: () => void;
  /** Backspace pressed in an empty field. */
  onBackspaceEmpty?: () => void;
  /** Render as a link in exported PDFs. */
  href?: string;
  /** Element kind and id, for selecting and styling it (see design.ts). */
  k?: string;
  i?: string;
  /** Enter adds a line break instead of finishing (free text boxes). */
  multiline?: boolean;
  /** Called after editing ends (the field lost focus). */
  onDone?: () => void;
  style?: React.CSSProperties;
}

/**
 * Click-to-edit text. Uncontrolled while focused (so the caret never jumps); commits on blur.
 * Ctrl/Cmd+B toggles bold in rich fields.
 */
export function Field({ id, value, onChange, rich = false, as: Tag = 'span', className, placeholder, onEnter, onBackspaceEmpty, href, k, i, multiline = false, onDone, style }: Props) {
  const { editable } = useContext(EditContext);
  const ref = useRef<HTMLElement>(null);
  const toHtml = rich ? richToHtml : escapeHtml;

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || document.activeElement === el) return;
    const html = toHtml(value);
    if (el.innerHTML !== html) el.innerHTML = html;
  });

  const data = { 'data-k': k, 'data-i': i };
  if (!editable) {
    const inner = <Tag className={className} style={style} {...data} dangerouslySetInnerHTML={{ __html: toHtml(value) }} />;
    return href ? <a href={href} className="plain-link">{inner}</a> : inner;
  }

  const commit = () => {
    const el = ref.current;
    if (!el) return;
    const next = htmlToRich(el, rich, multiline);
    if (next !== value) onChange(next);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLElement>) => {
    if (e.key === 'Enter' && multiline && !onEnter) {
      e.preventDefault();
      document.execCommand('insertLineBreak');
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (onEnter) {
        commit();
        onEnter();
      } else {
        ref.current?.blur();
      }
    } else if (e.key === 'Backspace' && onBackspaceEmpty && !(ref.current?.textContent ?? '').length) {
      e.preventDefault();
      onBackspaceEmpty();
    } else if (e.key === 'Escape') {
      ref.current?.blur();
    } else if ((e.key === 'b' || e.key === 'B') && (e.ctrlKey || e.metaKey) && !rich) {
      e.preventDefault();
    }
  };

  return (
    <Tag
      ref={ref}
      data-edit-id={id}
      data-placeholder={placeholder}
      className={`${className ?? ''} editable`}
      contentEditable
      suppressContentEditableWarning
      spellCheck
      {...data}
      style={style}
      onBlur={() => {
        commit();
        onDone?.();
      }}
      onKeyDown={onKeyDown}
      onPaste={(e: React.ClipboardEvent) => {
        e.preventDefault();
        const text = e.clipboardData.getData('text/plain');
        document.execCommand('insertText', false, multiline ? text.replace(/[^\S\n]+/g, ' ') : text.replace(/\s+/g, ' '));
      }}
    />
  );
}

export function focusField(id: string) {
  const el = document.querySelector<HTMLElement>(`[data-edit-id="${CSS.escape(id)}"]`);
  if (!el) return;
  el.focus();
  const range = document.createRange();
  range.selectNodeContents(el);
  range.collapse(false);
  const sel = window.getSelection();
  sel?.removeAllRanges();
  sel?.addRange(range);
}
