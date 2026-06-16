from __future__ import annotations

from algotrading.infra.risk import (
    AccountReconciliationReport,
    CashReconLine,
    FillReconLine,
    PositionReconLine,
    ReconStatusCounts,
)


def _position_line_to_dict(line: PositionReconLine) -> dict[str, object]:
    return {
        "join_key": line.join_key,
        "broker_contract_key": line.broker_contract_key,
        "book_contract_key": line.book_contract_key,
        "broker_quantity": line.broker_quantity,
        "book_quantity": line.book_quantity,
        "quantity_diff": line.quantity_diff,
        "status": line.status,
        "threshold": line.threshold,
        "threshold_version": line.threshold_version,
    }


def _cash_line_to_dict(line: CashReconLine) -> dict[str, object]:
    return {
        "currency": line.currency,
        "broker_cash_balance": line.broker_cash_balance,
        "broker_settled_cash": line.broker_settled_cash,
        "broker_net_liquidation": line.broker_net_liquidation,
        "status": line.status,
        "threshold_version": line.threshold_version,
    }


def _fill_line_to_dict(line: FillReconLine) -> dict[str, object]:
    return {
        "join_key": line.join_key,
        "broker_contract_key": line.broker_contract_key,
        "book_contract_key": line.book_contract_key,
        "broker_signed_quantity": line.broker_signed_quantity,
        "book_signed_quantity": line.book_signed_quantity,
        "quantity_diff": line.quantity_diff,
        "status": line.status,
        "threshold": line.threshold,
        "threshold_version": line.threshold_version,
    }


def _counts_to_dict(counts: ReconStatusCounts) -> dict[str, int]:
    return {
        "match": counts.match,
        "break": counts.breaks,
        "broker_only": counts.broker_only,
        "book_only": counts.book_only,
    }


def reconciliation_report_to_dict(report: AccountReconciliationReport) -> dict[str, object]:
    return {
        "account_id": report.account_id,
        "as_of_ts": report.as_of_ts.isoformat(),
        "book_source": report.book_source,
        "book_source_ts": report.book_source_ts.isoformat(),
        "threshold_version": report.threshold_version,
        "ok": report.ok,
        "positions": {
            "counts": _counts_to_dict(report.position_counts),
            "n_lines": len(report.position_lines),
            "lines": [_position_line_to_dict(line) for line in report.position_lines],
        },
        "cash": {
            "counts": _counts_to_dict(report.cash_counts),
            "n_lines": len(report.cash_lines),
            "lines": [_cash_line_to_dict(line) for line in report.cash_lines],
        },
        "fills": {
            "counts": _counts_to_dict(report.fill_counts),
            "n_lines": len(report.fill_lines),
            "lines": [_fill_line_to_dict(line) for line in report.fill_lines],
        },
    }
