import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { Link, navigate, parseRoute, paths, useRoute } from "./router";

describe("parseRoute", () => {
  it("knows the dashboard's pages", () => {
    expect(parseRoute("/")).toEqual({ name: "runs" });
    expect(parseRoute("/runs/20261104-120000-ab12")).toEqual({
      name: "run",
      runId: "20261104-120000-ab12",
      tab: "overview",
    });
    expect(parseRoute("/runs/R/plan")).toEqual({ name: "run", runId: "R", tab: "plan" });
    expect(parseRoute("/runs/R/units/u-0003?attempt=2")).toEqual({
      name: "unit",
      runId: "R",
      unitId: "U-0003",
      attempt: 2,
    });
    expect(parseRoute("/runs/R/units/U-0003?attempt=zero")).toMatchObject({ attempt: undefined });
    expect(parseRoute("/runs/R/nope")).toEqual({ name: "notFound", path: "/runs/R/nope" });
    expect(parseRoute("/elsewhere")).toMatchObject({ name: "notFound" });
  });

  it("builds the paths it parses", () => {
    expect(paths.run("R")).toBe("/runs/R");
    expect(paths.run("R", "graph")).toBe("/runs/R/graph");
    expect(paths.unit("R", "U-0001", 3)).toBe("/runs/R/units/U-0001?attempt=3");
    expect(parseRoute(paths.unit("R", "U-0001", 3))).toMatchObject({ unitId: "U-0001", attempt: 3 });
  });
});

function Where() {
  const route = useRoute();
  return <p>route: {route.name}</p>;
}

describe("navigation", () => {
  it("moves between pages without reloading", async () => {
    render(
      <>
        <Where />
        <Link to={paths.run("R")}>open</Link>
      </>,
    );
    expect(screen.getByText("route: runs")).toBeInTheDocument();
    await userEvent.click(screen.getByText("open"));
    expect(window.location.pathname).toBe("/runs/R");
    expect(screen.getByText("route: run")).toBeInTheDocument();
    act(() => navigate("/"));
    expect(screen.getByText("route: runs")).toBeInTheDocument();
  });
});
