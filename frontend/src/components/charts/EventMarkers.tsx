/**
 * Shared on-curve event markers for Recharts time-series charts.
 *
 * Replaces the old pattern of truncated ReferenceLine text labels (which
 * overlapped badly when events clustered) with a badge dot anchored on the
 * series curve plus a hover card carrying the FULL event details — labels,
 * amounts, dates, probabilities. Same-x events collapse into one badge whose
 * glyph is the count.
 *
 * Usage (inside a page component):
 *   const { markers, overlay, wrapperProps } = useEventMarkers(groups, {
 *     defaultColor: "var(--yellow)",
 *     xLabel: (x) => `Month ${x}`,
 *   });
 *   ...
 *   <div {...wrapperProps}>
 *     <ResponsiveContainer>…{markers}…</ResponsiveContainer>
 *     {overlay}
 *   </div>
 *
 * Gotcha: all our charts use category X axes, so each group's `x` MUST be a
 * value taken from the chart's own data array (exact match) — never a rounded
 * or recomputed value, or Recharts silently drops the marker.
 */

import { useMemo, useRef, useState } from "react";
import type { ReactElement } from "react";
import { ReferenceDot, ReferenceLine } from "recharts";
import { fmtCompact } from "../../utils/formatting";
import { TOOLTIP_STYLE } from "../../utils/theme";
import { parseLocalDate } from "../../utils/dates";

export interface ChartEventDetail {
  /** Full event name — never truncated. */
  label: string;
  /** Signed dollars (+income / −expense). Omit when unknown. */
  amount?: number;
  /** ISO date for the detail sub-line. */
  date?: string;
  /** 0..1 — shown as "N% likely" when < 1. */
  probability?: number;
  /** Per-event accent color (falls back to the group/default color). */
  color?: string;
}

export interface EventMarkerGroup {
  /** Must exactly equal a chart data point's x value (category axes). */
  x: string | number;
  /** Anchor value in left-axis units (the series the badge sits on). */
  y: number;
  events: ChartEventDetail[];
}

interface EventMarkerOptions {
  /** Badge color when events don't carry their own. Default: var(--yellow). */
  defaultColor?: string;
  /** Formats the group x value for the card header (e.g. month → "Aug 2027"). */
  xLabel?: (x: string | number) => string;
}

interface ActiveMarker {
  group: EventMarkerGroup;
  color: string;
  cx: number;
  cy: number;
}

/** Resolve one color for a group: unanimous event color, else the default. */
function groupColor(group: EventMarkerGroup, defaultColor: string): string {
  const colors = [...new Set(group.events.map((e) => e.color).filter(Boolean))];
  return colors.length === 1 ? (colors[0] as string) : defaultColor;
}

/** Badge glyph: count when grouped, sign when the amount is known, else none. */
function groupGlyph(group: EventMarkerGroup): string {
  if (group.events.length > 1) return String(group.events.length);
  const amount = group.events[0]?.amount;
  if (amount == null || amount === 0) return "";
  return amount > 0 ? "+" : "−";
}

/**
 * The badge shape passed to ReferenceDot. Recharts clones the element and
 * injects cx/cy; our custom props survive the clone.
 */
function EventBadge(props: {
  cx?: number;
  cy?: number;
  color: string;
  glyph: string;
  ariaLabel: string;
  onEnter: (cx: number, cy: number) => void;
  onLeave: () => void;
}) {
  const { cx, cy, color, glyph, ariaLabel, onEnter, onLeave } = props;
  if (cx == null || cy == null) return <g />;
  return (
    <g
      transform={`translate(${cx}, ${cy})`}
      style={{ pointerEvents: "all", cursor: "pointer" }}
      role="img"
      aria-label={ariaLabel}
      onMouseEnter={() => onEnter(cx, cy)}
      onMouseLeave={onLeave}
    >
      {/* Halo — makes the pin findable without shouting */}
      <circle r={9} fill={color} opacity={0.16} />
      <circle r={5.5} fill={color} stroke="#ffffff" strokeWidth={1.5} />
      {glyph && (
        <text
          textAnchor="middle"
          dominantBaseline="central"
          fill="#ffffff"
          fontSize={8}
          fontWeight={700}
          style={{ pointerEvents: "none", userSelect: "none" }}
        >
          {glyph}
        </text>
      )}
    </g>
  );
}

function fmtEventDate(iso: string): string {
  const d = parseLocalDate(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

const MAX_CARD_EVENTS = 6;

function EventCard({ active, xLabel, wrapperWidth }: {
  active: ActiveMarker;
  xLabel?: (x: string | number) => string;
  wrapperWidth: number;
}) {
  const { group, color, cx, cy } = active;
  const flipBelow = cy < 90;
  const left = Math.min(Math.max(cx, 90), Math.max(wrapperWidth - 90, 90));
  const shown = group.events.slice(0, MAX_CARD_EVENTS);
  const hidden = group.events.length - shown.length;

  return (
    <div
      className="pointer-events-none absolute z-20"
      style={{
        left,
        top: flipBelow ? cy + 14 : cy - 14,
        transform: flipBelow ? "translate(-50%, 0)" : "translate(-50%, -100%)",
        ...TOOLTIP_STYLE.contentStyle,
        padding: "8px 10px",
        minWidth: 150,
        maxWidth: 260,
      }}
    >
      <div style={{ ...TOOLTIP_STYLE.labelStyle, textTransform: "uppercase", letterSpacing: "0.05em", fontSize: "10px" }}>
        {xLabel ? xLabel(group.x) : String(group.x)}
      </div>
      {shown.map((ev, i) => {
        const sub: string[] = [];
        if (ev.date) sub.push(fmtEventDate(ev.date));
        if (ev.probability != null && ev.probability < 1) {
          sub.push(`${(ev.probability * 100).toFixed(0)}% likely`);
        }
        return (
          <div key={i} style={{ marginTop: i === 0 ? 0 : 5 }}>
            <div style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
              <span
                style={{
                  width: 7,
                  height: 7,
                  borderRadius: "50%",
                  background: ev.color ?? color,
                  flexShrink: 0,
                  alignSelf: "center",
                }}
              />
              <span style={{ fontSize: 12, fontWeight: 500, color: "#1a1a1e", lineHeight: 1.3 }}>
                {ev.label}
              </span>
              {ev.amount != null && ev.amount !== 0 && (
                <span
                  className="font-mono"
                  style={{
                    fontSize: 12,
                    fontWeight: 600,
                    marginLeft: "auto",
                    paddingLeft: 8,
                    color: ev.amount > 0 ? "var(--green)" : "var(--red)",
                    whiteSpace: "nowrap",
                  }}
                >
                  {ev.amount > 0 ? "+" : "−"}{fmtCompact(Math.abs(ev.amount))}
                </span>
              )}
            </div>
            {sub.length > 0 && (
              <div style={{ fontSize: 10, color: "#5c5c6a", marginLeft: 13, marginTop: 1 }}>
                {sub.join(" · ")}
              </div>
            )}
          </div>
        );
      })}
      {hidden > 0 && (
        <div style={{ fontSize: 10, color: "#5c5c6a", marginTop: 4 }}>+{hidden} more</div>
      )}
    </div>
  );
}

export function useEventMarkers(
  groups: EventMarkerGroup[],
  opts?: EventMarkerOptions,
): {
  markers: ReactElement[];
  overlay: ReactElement | null;
  wrapperProps: { ref: React.RefObject<HTMLDivElement | null>; className: string };
} {
  const defaultColor = opts?.defaultColor ?? "var(--yellow)";
  const wrapperRef = useRef<HTMLDivElement | null>(null);
  const [active, setActive] = useState<ActiveMarker | null>(null);

  const markers = useMemo(() => {
    const els: ReactElement[] = [];
    for (const group of groups) {
      const color = groupColor(group, defaultColor);
      const ariaLabel = group.events.map((e) => e.label).join("; ");
      els.push(
        <ReferenceLine
          key={`ev-line-${group.x}`}
          x={group.x}
          stroke={color}
          strokeDasharray="3 6"
          strokeOpacity={0.25}
        />,
        <ReferenceDot
          key={`ev-dot-${group.x}`}
          x={group.x}
          y={group.y}
          r={9}
          // Above Recharts' activeDot layer (1200) — the crosshair dot lands on
          // the same data point as the badge and would intercept hover otherwise.
          zIndex={1300}
          shape={
            <EventBadge
              color={color}
              glyph={groupGlyph(group)}
              ariaLabel={ariaLabel}
              onEnter={(cx, cy) => setActive({ group, color, cx, cy })}
              onLeave={() => setActive(null)}
            />
          }
        />,
      );
    }
    return els;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [groups, defaultColor]);

  const overlay = active ? (
    <EventCard
      active={active}
      xLabel={opts?.xLabel}
      wrapperWidth={wrapperRef.current?.clientWidth ?? 600}
    />
  ) : null;

  return {
    markers,
    overlay,
    // fm-event-hover hides the Recharts series tooltip (index.css) so the
    // event card and the crosshair tooltip never stack on screen together.
    wrapperProps: {
      ref: wrapperRef,
      className: active ? "relative fm-event-hover" : "relative",
    },
  };
}
