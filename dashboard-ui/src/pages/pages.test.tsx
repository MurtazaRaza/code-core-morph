import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { attemptView, detail, planView, RUN_ID, summary, unitView } from "../test/fixtures";
import { mockApi, renderWithClient } from "../test/helpers";
import { PlanPanel } from "./PlanPanel";
import { RunPage } from "./RunPage";
import { RunsPage } from "./RunsPage";
import { UnitPage } from "./UnitPage";

vi.mock("../components/DiffView", () => ({
  default: ({ language }: { language: string }) => <div data-testid="diff-view">{language}</div>,
}));
vi.mock("../components/GraphView", () => ({ default: () => <div data-testid="graph-canvas" /> }));

const RUN = `/api/runs/${RUN_ID}`;

describe("RunsPage", () => {
  it("lists runs with what they wait for", async () => {
    mockApi({
      "GET /api/runs": [summary({ waiting: ["plan"], status: "awaiting_plan_approval" })],
    });
    renderWithClient(<RunsPage />);
    const link = await screen.findByRole("link", { name: RUN_ID });
    expect(link).toHaveAttribute("href", `/runs/${RUN_ID}`);
    expect(screen.getByText("waiting for plan approval")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "plan approval" })).toHaveAttribute(
      "href",
      `/runs/${RUN_ID}/plan`,
    );
    expect(screen.getByText("1/3")).toBeInTheDocument();
    expect(screen.getByText("running")).toBeInTheDocument();
  });

  it("explains an empty repository", async () => {
    mockApi({ "GET /api/runs": [] });
    renderWithClient(<RunsPage />);
    expect(await screen.findByText("No runs yet")).toBeInTheDocument();
  });

  it("asks for the launch link when the browser has no access", async () => {
    mockApi({ "GET /api/runs": [401, { error: "this browser hasn't opened this launch's link" }] });
    renderWithClient(<RunsPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("hasn't opened this dashboard's link");
  });
});

describe("PlanPanel", () => {
  it("approves the plan without the units unticked", async () => {
    const calls = mockApi({
      [`GET ${RUN}/plan`]: planView(),
      [`POST ${RUN}/plan/decision`]: {
        run_id: RUN_ID,
        approval_id: 3,
        engine_alive: true,
        message: "Recorded. The run acts on it right away.",
      },
    });
    renderWithClient(<PlanPanel run={detail({ status: "awaiting_plan_approval" })} />);
    await userEvent.click(await screen.findByRole("checkbox", { name: "Run U-0002" }));
    await userEvent.type(screen.getByRole("textbox", { name: "Reason" }), "later");
    await userEvent.click(screen.getByRole("button", { name: "Approve without 1 unit" }));
    expect(await screen.findByRole("status")).toHaveTextContent("acts on it right away");
    const post = calls.find((c) => c.method === "POST");
    expect(post?.body).toEqual({ decision: "approve", reason: "later", exclude: ["U-0002"] });
  });

  it("rejects without exclusions and shows a refused decision", async () => {
    const calls = mockApi({
      [`GET ${RUN}/plan`]: planView(),
      [`POST ${RUN}/plan/decision`]: [409, { error: "run's plan was already decided" }],
    });
    renderWithClient(<PlanPanel run={detail({ status: "awaiting_plan_approval" })} />);
    await userEvent.click(await screen.findByRole("checkbox", { name: "Run U-0001" }));
    await userEvent.click(screen.getByRole("button", { name: "Reject the plan" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("already decided");
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({
      decision: "reject",
      reason: null,
      exclude: [],
    });
  });

  it("shows a decided plan without the gate", async () => {
    mockApi({ [`GET ${RUN}/plan`]: planView({ gate_open: false }) });
    renderWithClient(<PlanPanel run={detail()} />);
    expect(await screen.findByRole("heading", { name: "Units" })).toBeInTheDocument();
    expect(screen.queryByText("Plan gate")).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });

  it("says when there is no plan yet", async () => {
    mockApi({ [`GET ${RUN}/plan`]: [404, { error: `run ${RUN_ID} has no plan yet` }] });
    renderWithClient(<PlanPanel run={detail({ status: "baselining", plan_ready: false })} />);
    expect(await screen.findByText("No plan yet")).toBeInTheDocument();
  });
});

describe("RunPage", () => {
  it("shows what the run waits for and aborts it after asking", async () => {
    const calls = mockApi({
      [`GET ${RUN}`]: detail({
        waiting: ["U-0001"],
        engine_alive: false,
        events: [
          {
            id: 7,
            run_id: RUN_ID,
            ts: "2026-11-04T12:00:00+00:00",
            type: "unit.status",
            unit_id: "U-0001",
            attempt: null,
            data: { status: "awaiting_approval" },
          },
        ],
      }),
      [`POST ${RUN}/abort`]: {
        run_id: RUN_ID,
        approval_id: null,
        engine_alive: false,
        message: "The run was ended and its report written.",
      },
    });
    renderWithClient(<RunPage runId={RUN_ID} tab="overview" theme="light" />);
    expect(await screen.findByText("Unit U-0001 waits for your approval.")).toBeInTheDocument();
    expect(screen.getByText(`codecoremorph resume ${RUN_ID}`)).toBeInTheDocument();
    const activity = screen.getByRole("list", { name: "Latest events" });
    expect(within(activity).getByText("waiting for approval")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Abort…" }));
    await userEvent.type(screen.getByRole("textbox", { name: "Why the run is aborted" }), "no");
    await userEvent.click(screen.getByRole("button", { name: `Abort run ${RUN_ID}` }));
    expect(await screen.findByText("The run was ended and its report written.")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ reason: "no" });
  });

  it("has no abort for a finished run", async () => {
    mockApi({ [`GET ${RUN}`]: detail({ status: "completed", engine_alive: false }) });
    renderWithClient(<RunPage runId={RUN_ID} tab="overview" theme="light" />);
    expect(await screen.findByText("finished")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Abort…" })).not.toBeInTheDocument();
  });
});

describe("UnitPage", () => {
  it("keeps a unit waiting at its gate", async () => {
    const calls = mockApi({
      [`GET ${RUN}`]: detail({ waiting: ["U-0001"] }),
      [`GET ${RUN}/units/U-0001`]: unitView(),
      [`GET ${RUN}/units/U-0001/attempts/1`]: attemptView(),
      [`POST ${RUN}/units/U-0001/decision`]: {
        run_id: RUN_ID,
        approval_id: 4,
        engine_alive: true,
        message: "Recorded. The run acts on it right away.",
      },
    });
    renderWithClient(<UnitPage runId={RUN_ID} unitId="U-0001" attempt={undefined} theme="light" />);
    expect(await screen.findByText("This unit waits for your approval.")).toBeInTheDocument();
    expect(screen.getAllByText("it changes the public header include/geo.h")).not.toHaveLength(0);
    await userEvent.click(screen.getByRole("button", { name: "Keep the unit" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ decision: "approve", reason: null });
    expect(await screen.findByText("Recorded. The run acts on it right away.")).toBeInTheDocument();
  });

  it("shows an attempt's diff, compiler messages, tests, invariants and transcript", async () => {
    mockApi({
      [`GET ${RUN}`]: detail(),
      [`GET ${RUN}/units/U-0001`]: unitView({ gate: { waiting: false, reasons: [], commit: null, pending: null } }),
      [`GET ${RUN}/units/U-0001/attempts/1`]: attemptView(),
    });
    renderWithClient(<UnitPage runId={RUN_ID} unitId="U-0001" attempt={undefined} theme="light" />);
    expect(await screen.findByText("include/geo.h", { selector: "header span" })).toBeInTheDocument();
    expect(await screen.findByTestId("diff-view")).toHaveTextContent("c");

    await userEvent.click(screen.getByRole("tab", { name: /Compiler/ }));
    expect(screen.getByText("unknown type name 'Scalar'")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: /Tests/ }));
    expect(screen.getByText("gtest:Geo.Area")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: /Invariants/ }));
    expect(screen.getByText("needs approval")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "Transcript" }));
    expect(screen.getByText("Convert the typedefs.")).toBeInTheDocument();
    expect(screen.getByText("→ submit_edits")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "Feedback" }));
    expect(screen.getByText("The build failed.")).toBeInTheDocument();
  });
});
