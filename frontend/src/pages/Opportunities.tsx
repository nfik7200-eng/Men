import { PageHeader, Placeholder } from "../components/ui";

export default function Opportunities() {
  return (
    <>
      <PageHeader
        title="Opportunities"
        lede="New deployments, early liquidity, holder growth and developer activity, scored from evidence."
      />
      <Placeholder title="Opportunity detection is not available yet">
        Opportunity candidates need ingested chain data, Gold-layer features and the detectors in
        roadmap Stages 6–12. BIP shows no scores or recommendations until those exist, and each
        future score will list the evidence behind it.
      </Placeholder>
    </>
  );
}
