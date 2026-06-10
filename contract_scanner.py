import uuid
import logging
from datetime import datetime, date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from models import execute_query, execute_update, execute_many

logger = logging.getLogger(__name__)


def scan_active_contracts(batch_size=500):
    logger.info("Starting daily contract scan...")
    today = date.today().isoformat()

    contracts = execute_query(
        "SELECT contract_id, contract_no, customer_id, customer_name, "
        "effective_date, expiry_date, status FROM contracts WHERE status = 'active'",
        fetch_all=True
    )

    if not contracts:
        logger.info("No active contracts found")
        return {"scanned": 0, "flagged": 0, "expiring_soon": 0}

    logger.info(f"Found {len(contracts)} active contracts")

    expired_count = 0
    expiring_soon_count = 0
    flagged_milestones = 0

    expired_ids = []
    for c in contracts:
        if c["expiry_date"] < today:
            expired_ids.append((c["contract_id"],))
            expired_count += 1
        elif (date.fromisoformat(c["expiry_date"]) - date.today()).days <= 30:
            expiring_soon_count += 1

    if expired_ids:
        execute_many(
            "UPDATE contracts SET status = 'expired', updated_at = ? WHERE contract_id = ?",
            [(datetime.now().isoformat(), cid[0]) for cid in expired_ids]
        )
        logger.info(f"Marked {expired_count} contracts as expired")

    milestones = execute_query(
        "SELECT milestone_id, contract_id, contract_no, customer_id, "
        "milestone_type, planned_date, actual_date, amount, status, description "
        "FROM milestones WHERE status IN ('pending', 'upcoming_due')",
        fetch_all=True
    )

    updates = []
    for m in milestones:
        planned = date.fromisoformat(m["planned_date"])
        days_until = (planned - date.today()).days

        if m["status"] == "pending" and m["actual_date"] is None:
            if days_until < 0:
                new_status = "overdue"
                overdue_days = abs(days_until)
                updates.append((
                    new_status, overdue_days, datetime.now().isoformat(), m["milestone_id"]
                ))
                flagged_milestones += 1
            elif days_until <= 7:
                new_status = "upcoming_due"
                updates.append((
                    new_status, 0, datetime.now().isoformat(), m["milestone_id"]
                ))
                flagged_milestones += 1
        elif m["status"] == "upcoming_due" and days_until < 0:
            new_status = "overdue"
            overdue_days = abs(days_until)
            updates.append((
                new_status, overdue_days, datetime.now().isoformat(), m["milestone_id"]
            ))
            flagged_milestones += 1

    if updates:
        execute_many(
            "UPDATE milestones SET status = ?, overdue_days = ?, updated_at = ? "
            "WHERE milestone_id = ?",
            updates
        )

    overdue_payment_milestones = execute_query(
        "SELECT COUNT(*) as cnt FROM milestones WHERE status = 'overdue' AND milestone_type = 'payment'",
        fetch_one=True
    )

    result = {
        "scanned": len(contracts),
        "flagged": flagged_milestones,
        "expiring_soon": expiring_soon_count,
        "expired": expired_count,
        "overdue_payments": overdue_payment_milestones["cnt"] if overdue_payment_milestones else 0
    }

    logger.info(f"Contract scan complete: {result}")
    return result


def get_upcoming_payment_milestones(days_before=7):
    today = date.today()
    target_date = (today + timedelta(days=days_before)).isoformat()

    return execute_query(
        "SELECT m.*, c.credit_level, c.sales_manager, c.sales_director, "
        "cu.customer_name, cu.contact_email, cu.credit_score "
        "FROM milestones m "
        "JOIN contracts c ON m.contract_id = c.contract_id "
        "JOIN customers cu ON m.customer_id = cu.customer_id "
        "WHERE m.milestone_type = 'payment' "
        "AND m.status IN ('pending', 'upcoming_due') "
        "AND m.planned_date <= ? AND m.planned_date >= ? "
        "ORDER BY m.planned_date ASC",
        [target_date, today.isoformat()],
        fetch_all=True
    )


def get_overdue_payment_milestones(min_days=15):
    return execute_query(
        "SELECT m.*, c.credit_level, c.sales_manager, c.sales_director, "
        "cu.customer_name, cu.contact_email, cu.overdue_count, cu.order_frozen "
        "FROM milestones m "
        "JOIN contracts c ON m.contract_id = c.contract_id "
        "JOIN customers cu ON m.customer_id = cu.customer_id "
        "WHERE m.milestone_type = 'payment' "
        "AND m.status = 'overdue' "
        "AND m.overdue_days >= ? "
        "ORDER BY m.overdue_days DESC",
        [min_days],
        fetch_all=True
    )


def extract_milestones_for_contract(contract_id):
    return execute_query(
        "SELECT * FROM milestones WHERE contract_id = ? ORDER BY planned_date ASC",
        [contract_id],
        fetch_all=True
    )
