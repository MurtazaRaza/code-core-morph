// The dependency graph (FR-12.3) with Cytoscape.js: units or files, colored by unit status.
// Edges point from a dependency to what depends on it, so the leaves the plan starts with
// are at the top. Loaded lazily.
import cytoscape from "cytoscape";
import { useEffect, useRef } from "react";

import { statusTone } from "../format";
import type { Theme } from "../theme";
import type { GraphView as Graph } from "../types";

export type GraphMode = "units" | "files";
export const NO_UNIT = "none";
const MAX_FIT_ZOOM = 1.2;

function basename(path: string): string {
  return path.slice(path.lastIndexOf("/") + 1);
}

function elements(graph: Graph, mode: GraphMode): cytoscape.ElementDefinition[] {
  if (mode === "units") {
    const ids = new Set(graph.units.map((u) => u.id));
    return [
      ...graph.units.map((u) => ({
        data: {
          id: u.id,
          label: `${u.id} ${u.rules.join(", ")}`,
          search: [u.id, ...u.rules, ...u.files].join(" ").toLowerCase(),
          status: u.status,
          tone: statusTone(u.status),
          unit: u.id,
        },
      })),
      ...graph.unit_edges
        .filter(([a, b]) => ids.has(a) && ids.has(b))
        .map(([dependent, dependency]) => ({
          data: { id: `${dependency}->${dependent}`, source: dependency, target: dependent },
        })),
    ];
  }
  const paths = new Set(graph.files.map((f) => f.path));
  return [
    ...graph.files.map((f) => ({
      data: {
        id: f.path,
        label: basename(f.path),
        search: [f.path, ...f.units].join(" ").toLowerCase(),
        status: f.status ?? NO_UNIT,
        tone: f.status ? statusTone(f.status) : "none",
        unit: f.units.length === 1 ? f.units[0] : undefined,
      },
    })),
    ...graph.edges
      .filter(([a, b]) => paths.has(a) && paths.has(b) && a !== b)
      .map(([dependent, dependency]) => ({
        data: { id: `${dependency}->${dependent}`, source: dependency, target: dependent },
      })),
  ];
}

function styles(): cytoscape.StylesheetJson {
  const css = getComputedStyle(document.documentElement);
  const v = (name: string) => css.getPropertyValue(name).trim() || "#888";
  const tones = ["ok", "bad", "busy", "wait", "idle", "muted"];
  return [
    {
      selector: "node",
      style: {
        "background-color": v("--surface-2"),
        "border-width": 1,
        "border-color": v("--border"),
        label: "data(label)",
        color: v("--text"),
        "font-size": 10,
        "text-valign": "bottom",
        "text-margin-y": 4,
        "text-wrap": "ellipsis",
        "text-max-width": "140px",
        width: 18,
        height: 18,
      },
    },
    ...tones.map((tone) => ({
      selector: `node[tone = "${tone}"]`,
      style: { "background-color": v(`--${tone}`), "border-color": v(`--${tone}`) },
    })),
    {
      selector: "edge",
      style: {
        width: 1,
        "line-color": v("--border"),
        "target-arrow-color": v("--border"),
        "target-arrow-shape": "triangle",
        "arrow-scale": 0.8,
        "curve-style": "bezier",
      },
    },
    { selector: ".dim", style: { opacity: 0.15 } },
    { selector: ".match", style: { "border-width": 3, "border-color": v("--accent") } },
    { selector: ".hidden", style: { display: "none" } },
    { selector: "node[unit]", style: { cursor: "pointer" } } as cytoscape.StylesheetJsonBlock,
  ];
}

export default function GraphView({
  graph,
  mode,
  query,
  hidden,
  theme,
  onOpenUnit,
}: {
  graph: Graph;
  mode: GraphMode;
  query: string;
  hidden: ReadonlySet<string>;
  theme: Theme;
  onOpenUnit: (unitId: string) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const cy = useRef<cytoscape.Core | null>(null);
  const open = useRef(onOpenUnit);
  open.current = onOpenUnit;

  useEffect(() => {
    if (!host.current) return;
    const instance = cytoscape({
      container: host.current,
      elements: elements(graph, mode),
      style: styles(),
      minZoom: 0.1,
      maxZoom: 3,
    });
    instance.on("tap", "node", (event) => {
      const unit = event.target.data("unit") as string | undefined;
      if (unit) open.current(unit);
    });
    const layout = instance.layout(
      mode === "units"
        ? { name: "breadthfirst", directed: true, spacingFactor: 1.15, padding: 24 }
        : { name: "cose", animate: false, padding: 24, nodeRepulsion: () => 9000 },
    );
    // Fitting a small graph would blow its few nodes up: keep labels at a readable size.
    layout.one("layoutstop", () => {
      if (instance.zoom() > MAX_FIT_ZOOM) {
        instance.zoom(MAX_FIT_ZOOM);
        instance.center();
      }
    });
    layout.run();
    cy.current = instance;
    return () => {
      instance.destroy();
      cy.current = null;
    };
  }, [graph, mode]);

  useEffect(() => {
    cy.current?.style(styles());
  }, [theme]);

  useEffect(() => {
    const instance = cy.current;
    if (!instance) return;
    const needle = query.trim().toLowerCase();
    instance.batch(() => {
      instance.nodes().forEach((node) => {
        node.toggleClass("hidden", hidden.has(node.data("status") as string));
        const match = needle !== "" && (node.data("search") as string).includes(needle);
        node.toggleClass("match", match);
        node.toggleClass("dim", needle !== "" && !match);
      });
    });
  }, [query, hidden, graph, mode]);

  return <div ref={host} className="graph-canvas" data-testid="graph-canvas" />;
}
