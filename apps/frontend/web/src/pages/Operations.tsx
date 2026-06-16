import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/ui/card";

import { AsyncBlock } from "../components/AsyncBlock";

export function OperationsPage() {
  return (
    <section className="page">
      <div className="page-header">
        <div>
          <p className="eyebrow">Capture & run health</p>
          <h1>Operations</h1>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Operations</CardTitle>
          <CardDescription>
            The capture, run-state and connectivity surface lands here.
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
