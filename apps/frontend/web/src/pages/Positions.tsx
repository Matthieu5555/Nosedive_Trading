import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/ui/card";

import { AsyncBlock } from "../components/AsyncBlock";

export function PositionsPage() {
  return (
    <section className="page">
      <div className="page-header">
        <div>
          <p className="eyebrow">Book & fills</p>
          <h1>Positions</h1>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Positions</CardTitle>
          <CardDescription>
            The fills-based book — open positions, cash and reconciliation — lands here.
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
