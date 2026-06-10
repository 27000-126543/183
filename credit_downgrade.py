import json
import logging
from datetime import datetime
from models import execute_query, execute_update

logger = logging.getLogger(__name__)


def check_and_trigger_downgrade(customer_id, config=None):
    if config is None:
        config = {}

    credit_config = config.get("credit", {})
    overdue_count_threshold = credit_config.get("downgrade_overdue_count", 3)
    overdue_amount_threshold = credit_config.get("downgrade_overdue_amount", 500000)

    customer = execute_query(
        "SELECT * FROM customers WHERE customer_id = ?",
        [customer_id],
        fetch_one=True
    )
    if not customer:
        return None

    overdue_stats = execute_query(
        "SELECT COUNT(*) as cnt, COALESCE(SUM(amount), 0) as total "
        "FROM milestones WHERE customer_id = ? AND status = 'overdue' AND milestone_type = 'payment'",
        [customer_id],
        fetch_one=True
    )

    total_overdue = execute_query(
        "SELECT COALESCE(SUM(amount), 0) as total "
        "FROM milestones WHERE customer_id = ? AND status = 'overdue' "
        "AND milestone_type = 'payment' AND overdue_days > 0",
        [customer_id],
        fetch_one=True
    )

    overdue_count = overdue_stats["cnt"] if overdue_stats else 0
    overdue_amount = total_overdue["total"] if total_overdue else 0.0

    execute_update(
        "UPDATE customers SET overdue_count = ?, total_overdue_amount = ?, updated_at = ? "
        "WHERE customer_id = ?",
        [overdue_count, overdue_amount, datetime.now().isoformat(), customer_id]
    )

    should_downgrade = False
    reason = ""

    if overdue_count >= overdue_count_threshold:
        should_downgrade = True
        reason = f"累计逾期{overdue_count}次，超过阈值{overdue_count_threshold}次"

    if overdue_amount >= overdue_amount_threshold:
        should_downgrade = True
        reason += f"; 累计逾期金额{overdue_amount:.2f}元，超过阈值{overdue_amount_threshold}元"

    if should_downgrade:
        old_level = customer["credit_level"]
        level_order = {"A": 0, "B": 1, "C": 2, "D": 3}
        new_level = old_level

        if level_order.get(old_level, 1) < level_order.get("D", 3):
            new_level_order = level_order[old_level] + 1
            for lvl, order in level_order.items():
                if order == new_level_order:
                    new_level = lvl
                    break

        if new_level != old_level:
            old_score = customer["credit_score"]
            new_score = max(0, old_score - 20)

            execute_update(
                "UPDATE customers SET credit_level = ?, credit_score = ?, "
                "risk_level = 'high', updated_at = ? WHERE customer_id = ?",
                [new_level, new_score, datetime.now().isoformat(), customer_id]
            )

            execute_update(
                "INSERT INTO credit_history "
                "(history_id, customer_id, old_level, new_level, old_score, new_score, reason, change_date) "
                "VALUES (?,?,?,?,?,?,?,datetime('now','localtime'))",
                [f"DGD{customer_id}{datetime.now().strftime('%Y%m%d%H%M%S%f')}",
                 customer_id, old_level, new_level, old_score, new_score,
                 f"信用降级: {reason}"]
            )

            execute_update(
                "UPDATE contracts SET credit_level = ?, updated_at = ? "
                "WHERE customer_id = ? AND status = 'active'",
                [new_level, datetime.now().isoformat(), customer_id]
            )

            _log_operation("credit_downgrade", "", customer_id,
                          f"信用降级: {old_level}->{new_level}, 原因: {reason}")

            logger.info(f"Customer {customer_id} credit downgraded: {old_level} -> {new_level}. Reason: {reason}")

            return {
                "customer_id": customer_id,
                "downgraded": True,
                "old_level": old_level,
                "new_level": new_level,
                "old_score": old_score,
                "new_score": new_score,
                "reason": reason
            }
        else:
            _log_operation("credit_downgrade_max", "", customer_id,
                          f"已是最低信用等级，无法继续降级。原因: {reason}")
            return {
                "customer_id": customer_id,
                "downgraded": False,
                "reason": f"Already at lowest level. {reason}"
            }

    return {
        "customer_id": customer_id,
        "downgraded": False,
        "overdue_count": overdue_count,
        "overdue_amount": overdue_amount
    }


def batch_check_downgrades(config=None):
    customers_with_overdue = execute_query(
        "SELECT DISTINCT customer_id FROM milestones "
        "WHERE status = 'overdue' AND milestone_type = 'payment'",
        fetch_all=True
    )

    results = []
    for c in customers_with_overdue:
        result = check_and_trigger_downgrade(c["customer_id"], config)
        if result and result.get("downgraded"):
            results.append(result)

    logger.info(f"Batch downgrade check: {len(results)} customers downgraded")
    return results


def get_risk_summary():
    risk_levels = execute_query(
        "SELECT risk_level, COUNT(*) as cnt FROM customers GROUP BY risk_level",
        fetch_all=True
    )

    credit_distribution = execute_query(
        "SELECT credit_level, COUNT(*) as cnt, AVG(credit_score) as avg_score "
        "FROM customers GROUP BY credit_level ORDER BY credit_level",
        fetch_all=True
    )

    high_risk_customers = execute_query(
        "SELECT customer_id, customer_name, credit_level, credit_score, "
        "overdue_count, total_overdue_amount, order_frozen "
        "FROM customers WHERE risk_level = 'high' OR order_frozen = 1 "
        "ORDER BY total_overdue_amount DESC",
        fetch_all=True
    )

    return {
        "risk_levels": risk_levels,
        "credit_distribution": credit_distribution,
        "high_risk_customers": high_risk_customers
    }


def _log_operation(op_type, contract_id, customer_id, details):
    execute_update(
        "INSERT INTO audit_logs (operation_type, contract_id, customer_id, operator, details, created_at) "
        "VALUES (?,?,?,?,?,datetime('now','localtime'))",
        [op_type, contract_id, customer_id, "system", details]
    )
