import logging
from datetime import datetime, date
from models import execute_query, execute_update

logger = logging.getLogger(__name__)

CREDIT_LEVEL_THRESHOLDS = {"A": 90, "B": 75, "C": 60, "D": 0}


def update_credit_score(customer_id, event_type, config=None):
    if config is None:
        config = {}

    credit_config = config.get("credit", {})
    score_on_time = credit_config.get("score_on_time", 5)
    score_minor_late = credit_config.get("score_minor_late", -5)
    score_major_late = credit_config.get("score_major_late", -15)

    customer = execute_query(
        "SELECT * FROM customers WHERE customer_id = ?",
        [customer_id],
        fetch_one=True
    )
    if not customer:
        logger.warning(f"Customer {customer_id} not found")
        return None

    old_score = customer["credit_score"]
    old_level = customer["credit_level"]

    if event_type == "on_time":
        delta = score_on_time
    elif event_type == "minor_late":
        delta = score_minor_late
    elif event_type == "major_late":
        delta = score_major_late
    else:
        delta = 0

    new_score = max(0, min(100, old_score + delta))

    new_level = _score_to_level(new_score, config)

    now = datetime.now().isoformat()
    execute_update(
        "UPDATE customers SET credit_score = ?, credit_level = ?, updated_at = ? "
        "WHERE customer_id = ?",
        [new_score, new_level, now, customer_id]
    )

    if new_level != old_level:
        _record_credit_history(customer_id, old_level, new_level, old_score, new_score,
                              f"信用评分变更: {event_type}, 分数从{old_score}变为{new_score}")
        logger.info(f"Customer {customer_id} credit level changed: {old_level} -> {new_level} "
                    f"(score: {old_score} -> {new_score})")

    _log_operation("credit_score_updated", "", customer_id,
                   f"信用评分更新: {event_type}, {old_score}->{new_score}, 等级{old_level}->{new_level}")

    return {
        "customer_id": customer_id,
        "old_score": old_score, "new_score": new_score,
        "old_level": old_level, "new_level": new_level,
        "delta": delta, "event": event_type
    }


def batch_update_scores_from_payments(config=None):
    if config is None:
        config = {}

    completed_payments = execute_query(
        "SELECT m.customer_id, m.overdue_days "
        "FROM milestones m "
        "WHERE m.milestone_type = 'payment' AND m.status = 'completed' "
        "AND date(m.updated_at) = date('now','localtime')",
        fetch_all=True
    )

    results = []
    for p in completed_payments:
        if p["overdue_days"] == 0:
            event = "on_time"
        elif p["overdue_days"] <= 15:
            event = "minor_late"
        else:
            event = "major_late"

        result = update_credit_score(p["customer_id"], event, config)
        if result:
            results.append(result)

    logger.info(f"Batch credit score update: {len(results)} customers updated")
    return results


def _score_to_level(score, config=None):
    if config is None:
        config = {}
    thresholds = config.get("credit", {}).get("level_thresholds", CREDIT_LEVEL_THRESHOLDS)

    if score >= thresholds.get("A", 90):
        return "A"
    elif score >= thresholds.get("B", 75):
        return "B"
    elif score >= thresholds.get("C", 60):
        return "C"
    else:
        return "D"


def _record_credit_history(customer_id, old_level, new_level, old_score, new_score, reason):
    execute_update(
        "INSERT INTO credit_history "
        "(history_id, customer_id, old_level, new_level, old_score, new_score, reason, change_date) "
        "VALUES (?,?,?,?,?,?,?,datetime('now','localtime'))",
        [f"CHH{customer_id}{datetime.now().strftime('%Y%m%d%H%M%S%f')}",
         customer_id, old_level, new_level, old_score, new_score, reason]
    )


def _log_operation(op_type, contract_id, customer_id, details):
    execute_update(
        "INSERT INTO audit_logs (operation_type, contract_id, customer_id, operator, details, created_at) "
        "VALUES (?,?,?,?,?,datetime('now','localtime'))",
        [op_type, contract_id, customer_id, "system", details]
    )


def get_customer_credit_profile(customer_id):
    customer = execute_query(
        "SELECT * FROM customers WHERE customer_id = ?",
        [customer_id],
        fetch_one=True
    )
    if not customer:
        return None

    history = execute_query(
        "SELECT * FROM credit_history WHERE customer_id = ? ORDER BY change_date DESC LIMIT 20",
        [customer_id],
        fetch_all=True
    )

    overdue_milestones = execute_query(
        "SELECT COUNT(*) as cnt, SUM(amount) as total "
        "FROM milestones WHERE customer_id = ? AND status = 'overdue' AND milestone_type = 'payment'",
        [customer_id],
        fetch_one=True
    )

    return {
        "customer": customer,
        "history": history,
        "current_overdue": overdue_milestones
    }
