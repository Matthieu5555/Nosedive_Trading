import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/ui/card";

import { AsyncBlock } from "../components/AsyncBlock";

export function StrategyPage() {
  return (
    <section className="page">
      <div className="page-header">
        <div>
          <p className="eyebrow">Strategy book</p>
          <h1>Strategy</h1>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Strategy</CardTitle>
          <CardDescription>
            The composed strategy book — combined Greeks, stress and attribution — lands here.
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
