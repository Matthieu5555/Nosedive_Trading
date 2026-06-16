import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/ui/card";

import { AsyncBlock } from "../components/AsyncBlock";

export function SignalsPage() {
  return (
    <section className="page">
      <div className="page-header">
        <div>
          <p className="eyebrow">Strategy signal layer</p>
          <h1>Signals</h1>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Signals</CardTitle>
          <CardDescription>
            The persisted signal layer — implied correlation, IV rank, RV−IV, term slope — lands
            here.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <AsyncBlock loading={false} error={null}>
            <div className="state-panel" role="status">
              No data yet
            </div>
          </AsyncBlock>
        </CardContent>
      </Card>
    </section>
  );
}
