import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

/**
 * A small "i" that explains the number next to it. Opens on mouse hover or keyboard
 * focus; a tap or click keeps it open until you tap elsewhere or press Escape (touch
 * screens have no hover).
 *
 * The bubble is portalled to <body> in viewport coordinates, so an overflow-hidden card
 * can't clip it, and it is clamped to the screen so it never runs off an edge on a phone.
 * Clicks never reach a clickable parent (the property cards select on click).
 */
export default function InfoTip({ label, children }: { label: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const [pinned, setPinned] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const bubbleRef = useRef<HTMLDivElement>(null);
  const id = useId();

  const close = () => {
    setOpen(false);
    setPinned(false);
  };

  // Place below the icon (above when there's no room), clamped to the viewport.
  // Written straight to the element so the first paint is already in place.
  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const t = triggerRef.current?.getBoundingClientRect();
      const b = bubbleRef.current;
      if (!t || !b) return;
      const margin = 8;
      const gap = 6;
      const vw = document.documentElement.clientWidth;
      const left = Math.max(margin, Math.min(t.left + t.width / 2 - b.offsetWidth / 2, vw - b.offsetWidth - margin));
      const below = t.bottom + gap;
      const above = t.top - gap - b.offsetHeight;
      const top = below + b.offsetHeight + margin > window.innerHeight && above >= margin ? above : below;
      b.style.left = `${left}px`;
      b.style.top = `${top}px`;
      b.style.visibility = "visible";
    };
    place();
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    return () => {
      window.removeEventListener("scroll", place, true);
      window.removeEventListener("resize", place);
    };
  }, [open]);

  // While open: a tap/click anywhere else, or Escape, closes it.
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: PointerEvent) => {
      const target = e.target as Node;
      if (!triggerRef.current?.contains(target) && !bubbleRef.current?.contains(target)) close();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        aria-label={`What “${label}” means`}
        aria-expanded={open}
        aria-describedby={open ? id : undefined}
        onPointerEnter={(e) => e.pointerType === "mouse" && setOpen(true)}
        onPointerLeave={(e) => e.pointerType === "mouse" && !pinned && setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => !pinned && setOpen(false)}
        onClick={(e) => {
          e.stopPropagation();
          if (open && pinned) close();
          else {
            setOpen(true);
            setPinned(true);
          }
        }}
        // The ::before widens the tap target to ~30px without moving the layout.
        className="relative inline-flex items-center justify-center align-[-2px] ml-1 rounded-full text-[var(--text-tertiary)] hover:text-[var(--text-primary)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--blue)] before:absolute before:-inset-2 before:content-['']"
      >
        <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
          <circle cx="8" cy="8" r="7" fill="none" stroke="currentColor" strokeWidth="1.3" />
          <circle cx="8" cy="4.9" r="0.95" fill="currentColor" />
          <path d="M8 7.2v4.4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
      </button>
      {open &&
        createPortal(
          <div
            ref={bubbleRef}
            id={id}
            role="tooltip"
            onClick={(e) => e.stopPropagation()}
            style={{ top: 0, left: 0, visibility: "hidden" }}
            className="fixed z-50 w-72 max-w-[calc(100vw-16px)] rounded-lg border border-[var(--border)] bg-[var(--bg-elevated)] px-3 py-2.5 text-left text-[12.5px] font-normal normal-case tracking-normal leading-relaxed text-[var(--text-secondary)] shadow-[var(--shadow-elevated)] space-y-1.5"
          >
            <div className="font-semibold text-[var(--text-primary)]">{label}</div>
            {children}
          </div>,
          document.body,
        )}
    </>
  );
}
