import uuid
import logging
from datetime import datetime, date
from models import execute_query, execute_update, execute_many

logger = logging.getLogger(__name__)


def generate_collection_reminders(upcoming_milestones, config):
    if not upcoming_milestones:
        logger.info("No upcoming payment milestones for collection reminders")
        return {"generated": 0}

    high_credit = config.get("collection", {}).get("high_credit_levels", ["A", "B"])
    low_credit = config.get("collection", {}).get("low_credit_levels", ["C", "D"])
    gentle_template = config.get("collection", {}).get("gentle_email_template", "")
    firm_template = config.get("collection", {}).get("firm_letter_template", "")

    reminders = []
    generated = 0

    for m in upcoming_milestones:
        credit_level = m.get("credit_level", "B")
        if credit_level in high_credit:
            customer_type = "gentle_email"
            customer_content = gentle_template.format(
                customer_name=m.get("customer_name", ""),
                contract_no=m.get("contract_no", ""),
                description=m.get("description", ""),
                amount=m.get("amount", 0),
                due_date=m.get("planned_date", "")
            )
        else:
            customer_type = "firm_letter"
            customer_content = firm_template.format(
                customer_name=m.get("customer_name", ""),
                contract_no=m.get("contract_no", ""),
                description=m.get("description", ""),
                amount=m.get("amount", 0),
                due_date=m.get("planned_date", "")
            )
        customer_recipient = m.get("contact_email", "")

        cust_name = m.get('customer_name', '')
        contract_no = m.get('contract_no', '')
        desc = m.get('description', '')
        amount = m.get('amount', 0)
        due = m.get('planned_date', '')
        method = '温和邮件' if customer_type == 'gentle_email' else '强硬信函'
        manager_content = (
            f"【待跟进提醒】客户{cust_name}合同{contract_no}"
            + f"付款里程碑\"{desc}\"（金额：{amount}元）"
            + f"将于{due}到期，请及时跟进催收。"
            + f"客户信用等级：{credit_level}，催收方式：{method}。"
        )
        manager_recipient = m.get("sales_manager", "")

        existing_customer = execute_query(
            "SELECT reminder_id FROM collection_reminders "
            "WHERE milestone_id = ? AND reminder_type = ? "
            "AND status IN ('pending', 'sent') "
            "AND date(created_at) = date('now','localtime')",
            [m["milestone_id"], customer_type],
            fetch_one=True
        )
        if not existing_customer:
            reminder_id = f"RMD{uuid.uuid4().hex[:12]}"
            reminders.append((
                reminder_id, m["contract_id"], m["customer_id"], m["milestone_id"],
                customer_type, customer_content, customer_recipient, None, "pending",
                datetime.now().isoformat()
            ))
            generated += 1

        existing_manager = execute_query(
            "SELECT reminder_id FROM collection_reminders "
            "WHERE milestone_id = ? AND reminder_type = 'manager_notification' "
            "AND status IN ('pending', 'sent') "
            "AND date(created_at) = date('now','localtime')",
            [m["milestone_id"]],
            fetch_one=True
        )
        if not existing_manager:
            manager_id = f"RMD{uuid.uuid4().hex[:12]}"
            reminders.append((
                manager_id, m["contract_id"], m["customer_id"], m["milestone_id"],
                "manager_notification", manager_content, manager_recipient, None, "pending",
                datetime.now().isoformat()
            ))
            generated += 1

    if reminders:
        execute_many(
            "INSERT INTO collection_reminders "
            "(reminder_id, contract_id, customer_id, milestone_id, "
            "reminder_type, content, recipient, sent_date, status, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            reminders
        )

    logger.info(f"Generated {generated} collection reminders")
    return {"generated": generated}


def send_pending_reminders(config):
    pending = execute_query(
        "SELECT * FROM collection_reminders WHERE status = 'pending'",
        fetch_all=True
    )

    if not pending:
        return {"sent": 0, "failed": 0}

    sent_count = 0
    failed_count = 0
    now = datetime.now().isoformat()

    for r in pending:
        try:
            _simulate_send(r, config)
            execute_update(
                "UPDATE collection_reminders SET status = 'sent', sent_date = ? "
                "WHERE reminder_id = ?",
                [now, r["reminder_id"]]
            )
            sent_count += 1

            _log_operation("collection_sent", r["contract_id"], r["customer_id"],
                          f"催收提醒已发送: {r['reminder_type']}, 里程碑: {r['milestone_id']}")
        except Exception as e:
            execute_update(
                "UPDATE collection_reminders SET status = 'failed' "
                "WHERE reminder_id = ?",
                [r["reminder_id"]]
            )
            failed_count += 1
            logger.error(f"Failed to send reminder {r['reminder_id']}: {e}")

    logger.info(f"Reminders sent: {sent_count}, failed: {failed_count}")
    return {"sent": sent_count, "failed": failed_count}


def _simulate_send(reminder, config):
    smtp_host = config.get("email", {}).get("smtp_host", "smtp.example.com")
    logger.info(f"[SIMULATED] Sending {reminder['reminder_type']} to {reminder['recipient']} "
                f"via {smtp_host}")


def _log_operation(op_type, contract_id, customer_id, details):
    execute_update(
        "INSERT INTO audit_logs (operation_type, contract_id, customer_id, operator, details, created_at) "
        "VALUES (?,?,?,?,?,datetime('now','localtime'))",
        [op_type, contract_id, customer_id, "system", details]
    )


def get_reminder_history(customer_id=None, contract_id=None, limit=100):
    sql = "SELECT * FROM collection_reminders WHERE 1=1"
    params = []
    if customer_id:
        sql += " AND customer_id = ?"
        params.append(customer_id)
    if contract_id:
        sql += " AND contract_id = ?"
        params.append(contract_id)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    return execute_query(sql, params, fetch_all=True)
