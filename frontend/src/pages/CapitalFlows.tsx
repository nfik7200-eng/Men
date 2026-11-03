import { PageHeader, Panel, Placeholder } from "../components/ui";

/** Shape of a future flow-graph edge (from Gold-layer wallet_funding_edges). Not populated yet. */
export type FlowEdge = {
  chain: string;
  from: string;
  to: string;
  amountRaw: string;
  evidenceCount: number;
};

export function FlowGraph({ edges }: { edges: FlowEdge[] }) {
  if (edges.length === 0) {
    return (
      <Placeholder title="No capital-flow data">
        Flow graphs are built from stored transfers once ingestion and the Gold feature layer
        exist (roadmap Stages 6–11). Nothing is drawn until then, so no flow is ever invented.
      </Placeholder>
    );
  }
  return null;
}

export default function CapitalFlows() {
  return (
    <>
      <PageHeader
        title="Capital Flows"
        lede="Large transfers, exchange inflows and outflows, stablecoin movements and funding paths."
      />
      <Panel title="Flow graph">
        <FlowGraph edges={[]} />
      </Panel>
      <div className="grid grid-3">
        <Placeholder title="Whale movements">Requires ingested transfers and size thresholds.</Placeholder>
        <Placeholder title="Exchange flows">Requires ingested transfers and evidenced exchange labels.</Placeholder>
        <Placeholder title="Stablecoin flows">Requires ingested ERC-20 and SPL token transfers.</Placeholder>
      </div>
    </>
  );
}
