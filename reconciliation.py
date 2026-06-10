import uuid
import logging
from datetime import datetime, date, timedelta
from models import execute_query, execute_update, execute_many

logger = logging.getLogger(__name__)


def reconcile_erp_deliveries():
    logger.info("Starting ERP delivery reconciliation...")

    erp_records = execute_query(
        "SELECT e.*, c.contract_no FROM erp_records e "
        "JOIN contracts c ON e.contract_id = c.contract_id "
        "WHERE e.delivery_status IN ('delivered', 'partial')",
        fetch_all=True
    )

    if not erp_records:
        logger.info("No ERP delivery records to reconcile")
        return {"reconciled": 0, "mismatches": 0}

    delivery_milestones = execute_query(
        "SELECT milestone_id, contract_id, planned_date, actual_date, status "
        "FROM milestones WHERE milestone_type = 'delivery'",
        fetch_all=True
    )

    ms_by_contract = {}
    for m in delivery_milestones:
        cid = m["contract_id"]
        if cid not in ms_by_contract:
            ms_by_contract[cid] = []
        ms_by_contract[cid].append(m)

    reconciled = 0
    mismatches = 0
    updates = []

    for erp in erp_records:
        cid = erp["contract_id"]
        milestones = ms_by_contract.get(cid, [])

        for m in milestones:
            if m["status"] == "pending" and erp["delivery_status"] == "delivered":
                if erp["delivery_date"]:
                    updates.append((
                        erp["delivery_date"], "completed", 0, datetime.now().isoformat(),
                        m["milestone_id"]
                    ))
                    reconciled += 1
                break
            elif m["status"] == "overdue" and erp["delivery_status"] == "delivered":
                if erp["delivery_date"]:
                    planned = date.fromisoformat(m["planned_date"])
                    actual = date.fromisoformat(erp["delivery_date"])
                    overdue = max(0, (actual - planned).days)
                    updates.append((
                        erp["delivery_date"], "completed", overdue, datetime.now().isoformat(),
                        m["milestone_id"]
                    ))
                    reconciled += 1
                break

    if updates:
        execute_many(
            "UPDATE milestones SET actual_date = ?, status = ?, overdue_days = ?, "
            "updated_at = ? WHERE milestone_id = ?",
            updates
        )

    result = {"reconciled": reconciled, "mismatches": mismatches}
    logger.info(f"ERP reconciliation complete: {result}")
    return result


def match_bank_payments(tolerance_ratio=0.05):
    logger.info("Starting bank payment matching...")

    unmatched_txns = execute_query(
        "SELECT transaction_id, customer_id, amount, transaction_date "
        "FROM bank_transactions WHERE matched = 0 "
        "ORDER BY transaction_date DESC",
        fetch_all=True
    )

    if not unmatched_txns:
        logger.info("No unmatched bank transactions found")
        return {"matched": 0, "unmatched": 0}

    pending_payments = execute_query(
        "SELECT milestone_id, contract_id, contract_no, customer_id, "
        "amount, planned_date, status FROM milestones "
        "WHERE milestone_type = 'payment' AND status IN ('pending', 'upcoming_due', 'overdue') "
        "ORDER BY planned_date ASC",
        fetch_all=True
    )

    pay_by_customer = {}
    for p in pending_payments:
        cid = p["customer_id"]
        if cid not in pay_by_customer:
            pay_by_customer[cid] = []
        pay_by_customer[cid].append(p)

    matched_count = 0
    milestone_updates = []
    txn_updates = []

    for txn in unmatched_txns:
        cust_payments = pay_by_customer.get(txn["customer_id"], [])
        best_match = None
        best_diff = float("inf")

        for p in cust_payments:
            if p["amount"] <= 0:
                continue
            diff = abs(txn["amount"] - p["amount"])
            tolerance = p["amount"] * tolerance_ratio

            if diff <= tolerance and diff < best_diff:
                best_match = p
                best_diff = diff

        if best_match:
            txn_date = txn["transaction_date"]
            planned_date = date.fromisoformat(best_match["planned_date"])
            actual = date.fromisoformat(txn_date)
            overdue = max(0, (actual - planned_date).days)

            milestone_updates.append((
                txn_date, "completed", overdue, datetime.now().isoformat(),
                best_match["milestone_id"]
            ))
            txn_updates.append((
                1, best_match["milestone_id"], txn["transaction_id"]
            ))

            cust_payments.remove(best_match)
            matched_count += 1

    if milestone_updates:
        execute_many(
            "UPDATE milestones SET actual_date = ?, status = ?, overdue_days = ?, "
            "updated_at = ? WHERE milestone_id = ?",
            milestone_updates
        )

    if txn_updates:
        execute_many(
            "UPDATE bank_transactions SET matched = ?, matched_milestone_id = ? "
            "WHERE transaction_id = ?",
            txn_updates
        )

    unmatched_count = len(unmatched_txns) - matched_count
    result = {"matched": matched_count, "unmatched": unmatched_count}
    logger.info(f"Bank payment matching complete: {result}")
    return result


def get_reconciliation_summary():
    payment_stats = execute_query(
        "SELECT status, COUNT(*) as cnt, SUM(amount) as total "
        "FROM milestones WHERE milestone_type = 'payment' "
        "GROUP BY status",
        fetch_all=True
    )

    delivery_stats = execute_query(
        "SELECT status, COUNT(*) as cnt FROM milestones WHERE milestone_type = 'delivery' "
        "GROUP BY status",
        fetch_all=True
    )

    bank_stats = execute_query(
        "SELECT matched, COUNT(*) as cnt, SUM(amount) as total "
        "FROM bank_transactions GROUP BY matched",
        fetch_all=True
    )

    return {
        "payment_stats": payment_stats,
        "delivery_stats": delivery_stats,
        "bank_stats": bank_stats
    }
