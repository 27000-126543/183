import uuid
import json
import logging
from datetime import datetime, date
from models import execute_query, execute_update, execute_many

logger = logging.getLogger(__name__)


def process_escalations(overdue_milestones, config):
    if not overdue_milestones:
        logger.info("No overdue milestones exceeding threshold for escalation")
        return {"escalated": 0, "frozen": 0}

    threshold_days = config.get("escalation", {}).get("overdue_threshold_days", 15)
    notify_roles = config.get("escalation", {}).get("notify_roles", ["sales_director", "legal"])
    freeze_enabled = config.get("escalation", {}).get("freeze_order_on_escalation", True)

    escalated = 0
    frozen = 0
    tickets = []

    for m in overdue_milestones:
        if m["overdue_days"] < threshold_days:
            continue

        existing = execute_query(
            "SELECT ticket_id FROM escalation_tickets "
            "WHERE milestone_id = ? AND status = 'open'",
            [m["milestone_id"]],
            fetch_one=True
        )
        if existing:
            continue

        ticket_id = f"ESC{uuid.uuid4().hex[:12]}"
        tickets.append((
            ticket_id, m["contract_id"], m["customer_id"], m["milestone_id"],
            1, json.dumps(notify_roles), 1 if freeze_enabled else 0,
            "open", datetime.now().isoformat(), None
        ))
        escalated += 1

        if freeze_enabled and not m.get("order_frozen"):
            execute_update(
                "UPDATE customers SET order_frozen = 1, updated_at = ? WHERE customer_id = ?",
                [datetime.now().isoformat(), m["customer_id"]]
            )
            frozen += 1
            logger.info(f"Customer {m['customer_id']} order approval frozen due to escalation")

    if tickets:
        execute_many(
            "INSERT INTO escalation_tickets "
            "(ticket_id, contract_id, customer_id, milestone_id, escalation_level, "
            "notified_roles, freeze_action, status, created_at, resolved_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            tickets
        )

    for t in tickets:
        _log_operation("escalation_created", t[1], t[2],
                       f"逾期升级工单: {t[0]}, 通知角色: {t[5]}, 冻结: {t[6]}")

    logger.info(f"Escalation processing complete: escalated={escalated}, frozen={frozen}")
    return {"escalated": escalated, "frozen": frozen}


def notify_escalation_stakeholders(config):
    open_tickets = execute_query(
        "SELECT e.*, cu.customer_name, c.contract_no, c.sales_director, "
        "m.amount, m.overdue_days, m.planned_date "
        "FROM escalation_tickets e "
        "JOIN customers cu ON e.customer_id = cu.customer_id "
        "JOIN contracts c ON e.contract_id = c.contract_id "
        "JOIN milestones m ON e.milestone_id = m.milestone_id "
        "WHERE e.status = 'open'",
        fetch_all=True
    )

    notified = 0
    for t in open_tickets:
        roles = t.get("notified_roles", [])
        if isinstance(roles, str):
            roles = json.loads(roles)

        for role in roles:
            if role == "sales_director":
                _send_notification(
                    t.get("sales_director", "销售总监"),
                    f"【紧急】合同{t['contract_no']}付款逾期{t['overdue_days']}天",
                    f"客户{t['customer_name']}合同{t['contract_no']}付款逾期{t['overdue_days']}天，"
                    f"金额{t['amount']}元，请立即处理。"
                )
            elif role == "legal":
                _send_notification(
                    "法务部",
                    f"【法务通知】合同{t['contract_no']}严重逾期",
                    f"客户{t['customer_name']}合同{t['contract_no']}付款逾期{t['overdue_days']}天，"
                    f"需法务介入评估。"
                )
            notified += 1

        execute_update(
            "UPDATE escalation_tickets SET status = 'notified' WHERE ticket_id = ?",
            [t["ticket_id"]]
        )

    logger.info(f"Notified {notified} stakeholders for escalation tickets")
    return {"notified": notified}


def resolve_escalation(ticket_id, resolution_note=""):
    execute_update(
        "UPDATE escalation_tickets SET status = 'resolved', resolved_at = ? "
        "WHERE ticket_id = ?",
        [datetime.now().isoformat(), ticket_id]
    )
    _log_operation("escalation_resolved", "", "",
                   f"升级工单已解决: {ticket_id}, 备注: {resolution_note}")
    logger.info(f"Escalation ticket {ticket_id} resolved")


def unfreeze_customer(customer_id):
    execute_update(
        "UPDATE customers SET order_frozen = 0, updated_at = ? WHERE customer_id = ?",
        [datetime.now().isoformat(), customer_id]
    )
    _log_operation("customer_unfreezed", "", customer_id, "客户订单审批权限已解冻")
    logger.info(f"Customer {customer_id} order approval unfrozen")


def _send_notification(recipient, subject, body):
    logger.info(f"[NOTIFICATION] To: {recipient} | Subject: {subject} | Body: {body}")


def _log_operation(op_type, contract_id, customer_id, details):
    execute_update(
        "INSERT INTO audit_logs (operation_type, contract_id, customer_id, operator, details, created_at) "
        "VALUES (?,?,?,?,?,datetime('now','localtime'))",
        [op_type, contract_id, customer_id, "system", details]
    )
