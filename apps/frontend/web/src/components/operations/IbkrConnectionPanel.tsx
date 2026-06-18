import "./operations.css";

import { ApiError, type IbkrStatus } from "../../api";
import { useIbkrConnect, useIbkrLogin, useIbkrStatus } from "../../hooks/queries";
import { AsyncBlock } from "../AsyncBlock";

type Tone = "ok" | "warn" | "bad";

// The shell hint the BFF echoes for the not-authenticated path. We highlight it as code when it
// appears inside a detail string so the operator can copy the one command that fixes the state.
const LOGIN_HINT = "scripts/ibkr_login.py";

function toneFor(status: IbkrStatus): Tone {
  if (!status.configured) return "warn";
  if (status.established) return "ok";
  if (status.authenticated) return "warn";
  return "bad";
}

function headlineFor(status: IbkrStatus): string {
  if (!status.configured) return "Gateway not configured";
  if (status.established) return "Session ready";
  if (status.authenticated) return "Authenticated, session not opened";
  return "Not authenticated";
}

// Render a detail string, surfacing any "scripts/ibkr_login.py" hint as inline code so the operator
// sees the exact shell command, not just prose.
function DetailLine({ detail }: { detail: string }) {
  const idx = detail.indexOf(LOGIN_HINT);
  if (idx === -1) {
    return <p className="ibkr-panel__detail panel-note">{detail}</p>;
  }
  return (
    <p className="ibkr-panel__detail panel-note">
      {detail.slice(0, idx)}
      <code className="ibkr-panel__hint">{LOGIN_HINT}</code>
      {detail.slice(idx + LOGIN_HINT.length)}
    </p>
  );
}

function errorDetail(error: unknown): string | null {
  if (error instanceof ApiError) return error.detail;
  if (error instanceof Error) return error.message;
  return null;
}

function StatusView({ status }: { status: IbkrStatus }) {
  const connect = useIbkrConnect();
  const login = useIbkrLogin();
  const tone = toneFor(status);
  // Opening a brokerage session only makes sense once authenticated at the SSO layer; before that
  // the path forward is the login button below, so Open is offered disabled with an honest title.
  const canConnect = status.authenticated && !status.established && !connect.isPending;
  // Log in is offered exactly in the state the operator complained about: a gateway is up but there
  // is no SSO session. It runs the idempotent scripts/ibkr_login.py on the server (auth only, never
  // trades); a 2FA challenge that cannot complete headless is surfaced honestly below.
  const canLogIn = status.configured && !status.authenticated && !login.isPending;
  const connectError = errorDetail(connect.error);
  const loginError = errorDetail(login.error);

  return (
    <div className="ibkr-panel">
      <div className="ibkr-panel__headline">
        <span className={`ops-light ops-light--${tone}`} aria-hidden="true" />
        <div className="ibkr-panel__summary">
          <p className="ibkr-panel__state">
            <span
              className={`ops-pill ops-pill--${tone}`}
              aria-label={`IBKR ${headlineFor(status)}`}
            >
              {headlineFor(status)}
            </span>
            {status.account && (
              <>
                {" "}
                <span className="ibkr-panel__account panel-note">account {status.account}</span>
              </>
            )}
          </p>
          <DetailLine detail={status.detail} />
        </div>
      </div>

      <div className="ibkr-panel__actions">
        <button
          type="button"
          className="ibkr-panel__connect ibkr-panel__login"
          disabled={!canLogIn}
          title={
            !status.configured
              ? "Bring up the gateway first (see the detail above)."
              : status.authenticated
                ? "Already authenticated; no login needed."
                : "Run the IBKR login on the server (auth only, never trades). Completes a 2FA challenge only from a shell."
          }
          onClick={() => login.mutate()}
        >
          {login.isPending ? "Logging in…" : "Log in to IBKR"}
        </button>
        <button
          type="button"
          className="ibkr-panel__connect"
          disabled={!canConnect}
          title={
            status.established
              ? "The brokerage session is already established."
              : status.authenticated
                ? "Open the brokerage session (ssodh/init) on the authenticated gateway."
                : "Log in to IBKR first, then open the brokerage session."
          }
          onClick={() => connect.mutate()}
        >
          {connect.isPending ? "Opening…" : "Open brokerage session"}
        </button>
      </div>

      {loginError && (
        <div role="alert" className="error">
          Could not log in to IBKR: <DetailLine detail={loginError} />
        </div>
      )}

      {connectError && (
        <div role="alert" className="error">
          Could not open the brokerage session: <DetailLine detail={connectError} />
        </div>
      )}
    </div>
  );
}

export function IbkrConnectionPanel() {
  const status = useIbkrStatus();

  return (
    <div className="ibkr-panel-wrap">
      <AsyncBlock loading={status.isPending} error={status.isError ? status.error.message : null}>
        {status.data && <StatusView status={status.data} />}
      </AsyncBlock>
      <div className="ibkr-panel__actions">
        <button
          type="button"
          className="ibkr-panel__refresh"
          disabled={status.isFetching}
          onClick={() => void status.refetch()}
        >
          {status.isFetching ? "Refreshing…" : "Refresh status"}
        </button>
      </div>
    </div>
  );
}
