import uuid
import json
import logging
from datetime import datetime, date
from models import execute_query, execute_update, execute_many

logger = logging.getLogger(__name__)


def submit_change_request(contract_id, customer_id, change_type, proposed_data, submitted_by="system"):
    valid_types = ["extension", "installment"]
    if change_type not in valid_types:
        raise ValueError(f"Invalid change type: {change_type}. Must be one of {valid_types}")

    contract = execute_query(
        "SELECT * FROM contracts WHERE contract_id = ? AND status = 'active'",
        [contract_id],
        fetch_one=True
    )
    if not contract:
        raise ValueError(f"Contract {contract_id} not found or not active")

    original_data = {}
    milestones = execute_query(
        "SELECT milestone_id, planned_date, amount, status, description "
        "FROM milestones WHERE contract_id = ? AND status IN ('pending', 'upcoming_due', 'overdue') "
        "ORDER BY planned_date ASC",
        [contract_id],
        fetch_all=True
    )

    if change_type == "extension":
        extension_days = proposed_data.get("extension_days", 0)
        if extension_days <= 0:
            raise ValueError("Extension days must be positive")
        target_milestone_id = proposed_data.get("milestone_id")
        if not target_milestone_id:
            raise ValueError("milestone_id is required for extension")

        target = next((m for m in milestones if m["milestone_id"] == target_milestone_id), None)
        if not target:
            raise ValueError(f"Milestone {target_milestone_id} not found in pending milestones")

        original_data = {
            "milestone_id": target["milestone_id"],
            "original_planned_date": target["planned_date"],
            "extension_days": extension_days
        }

    elif change_type == "installment":
        target_amount = proposed_data.get("amount", 0)
        installment_count = proposed_data.get("installment_count", 0)
        if target_amount <= 0 or installment_count < 2:
            raise ValueError("Amount must be positive and installment_count must be >= 2")

        target_milestone_id = proposed_data.get("milestone_id")
        if not target_milestone_id:
            raise ValueError("milestone_id is required for installment")

        target = next((m for m in milestones if m["milestone_id"] == target_milestone_id), None)
        if not target:
            raise ValueError(f"Milestone {target_milestone_id} not found")

        original_data = {
            "milestone_id": target["milestone_id"],
            "original_amount": target["amount"],
            "installment_count": installment_count,
            "per_installment": round(target_amount / installment_count, 2)
        }

    request_id = f"CHR{uuid.uuid4().hex[:12]}"
    now = datetime.now().isoformat()

    execute_update(
        "INSERT INTO change_requests "
        "(request_id, contract_id, customer_id, change_type, original_data, proposed_data, "
        "approval_status, current_approver_level, created_by, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        [request_id, contract_id, customer_id, change_type,
         json.dumps(original_data, ensure_ascii=False),
         json.dumps(proposed_data, ensure_ascii=False),
         "pending", 1, submitted_by, now]
    )

    _create_approval_flows(request_id, change_type, proposed_data)

    _log_operation("change_request_submitted", contract_id, customer_id,
                   f"变更申请提交: {change_type}, 申请ID: {request_id}")

    logger.info(f"Change request {request_id} submitted for contract {contract_id}")
    return {"request_id": request_id, "status": "pending_approval"}


def _create_approval_flows(request_id, change_type, proposed_data):
    approval_config = _get_approval_config()
    flows = []
    now = datetime.now().isoformat()

    for level_conf in approval_config:
        flow_id = f"APV{uuid.uuid4().hex[:12]}"
        flows.append((
            flow_id, request_id, level_conf["level"],
            level_conf["role"], "", "pending", "", None, now
        ))

    if flows:
        execute_many(
            "INSERT INTO approval_flows "
            "(flow_id, change_request_id, approver_level, approver_role, "
            "approver_id, status, comments, action_date, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            flows
        )


def _get_approval_config():
    return [
        {"level": 1, "role": "sales_manager", "max_extension_days": 30, "max_installment_count": 3},
        {"level": 2, "role": "sales_director", "max_extension_days": 90, "max_installment_count": 6},
        {"level": 3, "role": "general_manager", "max_extension_days": 365, "max_installment_count": 12},
    ]


def approve_change(flow_id, approver_id, comments=""):
    flow = execute_query(
        "SELECT * FROM approval_flows WHERE flow_id = ? AND status = 'pending'",
        [flow_id],
        fetch_one=True
    )
    if not flow:
        raise ValueError(f"Approval flow {flow_id} not found or already processed")

    request = execute_query(
        "SELECT * FROM change_requests WHERE request_id = ?",
        [flow["change_request_id"]],
        fetch_one=True
    )
    if not request:
        raise ValueError("Change request not found")

    if not _validate_approval_conditions(request, flow):
        raise ValueError("Approval conditions not met for this level")

    now = datetime.now().isoformat()
    execute_update(
        "UPDATE approval_flows SET status = 'approved', approver_id = ?, "
        "comments = ?, action_date = ? WHERE flow_id = ?",
        [approver_id, comments, now, flow_id]
    )

    next_level = flow["approver_level"] + 1
    next_flow = execute_query(
        "SELECT * FROM approval_flows "
        "WHERE change_request_id = ? AND approver_level = ? AND status = 'pending'",
        [flow["change_request_id"], next_level],
        fetch_one=True
    )

    if next_flow:
        execute_update(
            "UPDATE change_requests SET current_approver_level = ? WHERE request_id = ?",
            [next_level, flow["change_request_id"]]
        )
        logger.info(f"Change request {flow['change_request_id']} advanced to approval level {next_level}")
        return {"request_id": flow["change_request_id"], "status": "pending_approval", "next_level": next_level}
    else:
        _finalize_approval(request)
        return {"request_id": flow["change_request_id"], "status": "approved"}


def _validate_approval_conditions(request, flow):
    proposed = request.get("proposed_data", {})
    if isinstance(proposed, str):
        proposed = json.loads(proposed)

    approval_config = _get_approval_config()
    level_config = next((c for c in approval_config if c["level"] == flow["approver_level"]), None)
    if not level_config:
        return False

    if request["change_type"] == "extension":
        ext_days = proposed.get("extension_days", 0)
        if ext_days > level_config["max_extension_days"]:
            logger.warning(f"Extension {ext_days} days exceeds level {flow['approver_level']} max "
                          f"{level_config['max_extension_days']}")
            return False

    elif request["change_type"] == "installment":
        inst_count = proposed.get("installment_count", 0)
        if inst_count > level_config["max_installment_count"]:
            logger.warning(f"Installment count {inst_count} exceeds level {flow['approver_level']} max "
                          f"{level_config['max_installment_count']}")
            return False

    return True


def reject_change(flow_id, approver_id, reason=""):
    flow = execute_query(
        "SELECT * FROM approval_flows WHERE flow_id = ? AND status = 'pending'",
        [flow_id],
        fetch_one=True
    )
    if not flow:
        raise ValueError(f"Approval flow {flow_id} not found or already processed")

    now = datetime.now().isoformat()
    execute_update(
        "UPDATE approval_flows SET status = 'rejected', approver_id = ?, "
        "comments = ?, action_date = ? WHERE flow_id = ?",
        [approver_id, reason, now, flow_id]
    )

    execute_update(
        "UPDATE change_requests SET approval_status = 'rejected', "
        "rejected_at = ?, rejection_reason = ? WHERE request_id = ?",
        [now, reason, flow["change_request_id"]]
    )

    _log_operation("change_rejected", "", "",
                   f"变更申请被拒绝: {flow['change_request_id']}, 原因: {reason}")

    logger.info(f"Change request {flow['change_request_id']} rejected")
    return {"request_id": flow["change_request_id"], "status": "rejected"}


def _finalize_approval(request):
    now = datetime.now().isoformat()
    proposed = request.get("proposed_data", {})
    original = request.get("original_data", {})
    if isinstance(proposed, str):
        proposed = json.loads(proposed)
    if isinstance(original, str):
        original = json.loads(original)

    execute_update(
        "UPDATE change_requests SET approval_status = 'approved', approved_at = ? "
        "WHERE request_id = ?",
        [now, request["request_id"]]
    )

    if request["change_type"] == "extension":
        milestone_id = original.get("milestone_id")
        extension_days = proposed.get("extension_days", 0)
        current = execute_query(
            "SELECT planned_date FROM milestones WHERE milestone_id = ?",
            [milestone_id],
            fetch_one=True
        )
        if current:
            new_date = (date.fromisoformat(current["planned_date"]) + __import__("datetime").timedelta(days=extension_days)).isoformat()
            execute_update(
                "UPDATE milestones SET planned_date = ?, status = 'pending', "
                "overdue_days = 0, updated_at = ? WHERE milestone_id = ?",
                [new_date, now, milestone_id]
            )

    elif request["change_type"] == "installment":
        milestone_id = original.get("milestone_id")
        per_installment = original.get("per_installment", 0)
        installment_count = original.get("installment_count", 2)

        execute_update(
            "UPDATE milestones SET amount = ?, updated_at = ? WHERE milestone_id = ?",
            [per_installment, now, milestone_id]
        )

        current_ms = execute_query(
            "SELECT contract_id, contract_no, customer_id, planned_date, description "
            "FROM milestones WHERE milestone_id = ?",
            [milestone_id],
            fetch_one=True
        )
        if current_ms:
            base_date = date.fromisoformat(current_ms["planned_date"])
            new_milestones = []
            for i in range(1, installment_count):
                new_date = (base_date + __import__("datetime").timedelta(days=30 * i)).isoformat()
                new_mid = f"MS{uuid.uuid4().hex[:12]}"
                new_milestones.append((
                    new_mid, current_ms["contract_id"], current_ms["contract_no"],
                    current_ms["customer_id"], "payment", new_date, None,
                    per_installment, "pending",
                    f"{current_ms['description']}(分期{i + 1}/{installment_count})",
                    0, now, now
                ))
            if new_milestones:
                execute_many(
                    "INSERT INTO milestones "
                    "(milestone_id, contract_id, contract_no, customer_id, milestone_type, "
                    "planned_date, actual_date, amount, status, description, overdue_days, "
                    "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    new_milestones
                )

    _log_operation("change_approved", request["contract_id"], request["customer_id"],
                   f"变更申请审批通过: {request['change_type']}, 申请ID: {request['request_id']}")

    logger.info(f"Change request {request['request_id']} fully approved and milestones updated")


def get_pending_approvals(role=None, approver_id=None):
    sql = ("SELECT cr.*, af.flow_id, af.approver_level, af.approver_role "
           "FROM change_requests cr "
           "JOIN approval_flows af ON cr.request_id = af.change_request_id "
           "WHERE cr.approval_status = 'pending' AND af.status = 'pending'")
    params = []
    if role:
        sql += " AND af.approver_role = ?"
        params.append(role)
    if approver_id:
        sql += " AND af.approver_id = ?"
        params.append(approver_id)
    sql += " ORDER BY cr.created_at ASC"
    return execute_query(sql, params, fetch_all=True)


def _log_operation(op_type, contract_id, customer_id, details):
    execute_update(
        "INSERT INTO audit_logs (operation_type, contract_id, customer_id, operator, details, created_at) "
        "VALUES (?,?,?,?,?,datetime('now','localtime'))",
        [op_type, contract_id, customer_id, "system", details]
    )
