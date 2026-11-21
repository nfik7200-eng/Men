import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Address } from "./Address";
import { DataTable, type Column } from "./DataTable";
import { Grade } from "./Grade";

const ADDR = "0x00000000000000000000000000000000000000aa";

function mockNarrow(narrow: boolean) {
  vi.stubGlobal("matchMedia", (q: string) => ({
    matches: narrow && q.includes("max-width"),
    media: q,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
  }));
}

afterEach(() => vi.unstubAllGlobals());

describe("Address", () => {
  it("truncates in the middle, keeps the full value accessible and copies it", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    render(<Address value={ADDR} />);
    expect(screen.getByText("0x000000…0000aa")).toHaveAttribute("title", ADDR);
    await user.click(screen.getByRole("button", { name: "Copy address" }));
    expect(writeText).toHaveBeenCalledWith(ADDR);
    expect(await screen.findByText("Copied")).toBeInTheDocument();
  });

  it("shows short values whole", () => {
    render(<Address value="native" copy={false} />);
    expect(screen.getByText("native")).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
  });
});

type Row = { id: string; kind: string; amount: string };
const rows: Row[] = [
  { id: "tx1", kind: "transfer", amount: "10" },
  { id: "tx2", kind: "swap", amount: "—" },
];
const columns: Column<Row>[] = [
  { key: "id", header: "Transaction", render: (r) => r.id, priority: 1 },
  { key: "kind", header: "Kind", render: (r) => r.kind, priority: 2 },
  { key: "amount", header: "Amount", render: (r) => r.amount, priority: 3, numeric: true },
];

describe("DataTable", () => {
  it("renders a table on wide screens", () => {
    mockNarrow(false);
    render(<DataTable caption="Transfers" rows={rows} columns={columns} rowKey={(r) => r.id} />);
    const table = screen.getByRole("table", { name: "Transfers" });
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Transaction", "Kind", "Amount"]);
  });

  it("renders cards on narrow screens with the priority-1 column as the title", () => {
    mockNarrow(true);
    render(<DataTable caption="Transfers" rows={rows} columns={columns} rowKey={(r) => r.id} />);
    expect(screen.queryByRole("table")).toBeNull();
    const list = screen.getByRole("list", { name: "Transfers" });
    const cards = within(list).getAllByRole("listitem");
    expect(cards).toHaveLength(2);
    const first = within(cards[0] as HTMLElement);
    expect(first.getByRole("heading", { name: "tx1" })).toBeInTheDocument();
    expect(first.getByText("Kind")).toBeInTheDocument();
    expect(first.getByText("transfer")).toBeInTheDocument();
  });

  it("shows an empty message instead of an empty table", () => {
    mockNarrow(false);
    render(<DataTable caption="Transfers" rows={[]} columns={columns} rowKey={(r) => r.id} empty="No transfers stored" />);
    expect(screen.getByText("No transfers stored")).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
  });
});

describe("Grade", () => {
  it("always shows text, with a shape class per evidence kind", () => {
    const { container } = render(<Grade kind="model_prediction" />);
    expect(screen.getByText("Model prediction")).toBeInTheDocument();
    expect(container.querySelector(".grade-model_prediction")).not.toBeNull();
  });

  it("shows an unknown kind as plain text rather than guessing a grade", () => {
    render(<Grade kind="something_new" />);
    expect(screen.getByText("something new")).toBeInTheDocument();
  });
});
