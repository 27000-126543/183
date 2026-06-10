#!/usr/bin/env python3
import sys
import os
import argparse
import logging

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models import init_db, set_db_path, execute_query, execute_update
from db_init import init_and_seed, reset_db
from scheduler import ContractMonitorScheduler
from contract_scanner import (
    scan_active_contracts, get_upcoming_payment_milestones,
    get_overdue_payment_milestones, extract_milestones_for_contract
)
from reconciliation import reconcile_erp_deliveries, match_bank_payments, get_reconciliation_summary
from collection import generate_collection_reminders, send_pending_reminders, get_reminder_history
from escalation import process_escalations, notify_escalation_stakeholders, resolve_escalation, unfreeze_customer
from change_approval import (
    submit_change_request, approve_change, reject_change,
    get_pending_approvals
)
from credit_scoring import update_credit_score, batch_update_scores_from_payments, get_customer_credit_profile
from credit_downgrade import check_and_trigger_downgrade, batch_check_downgrades, get_risk_summary
from report import generate_monthly_report
from audit_log import log_operation, query_logs, batch_export_logs, get_log_statistics


def cmd_init(args):
    num_contracts = args.contracts or 500
    num_customers = args.customers or 50
    if args.reset:
        logging.info("Reset mode: clearing existing database and rebuilding...")
    init_and_seed(num_contracts, num_customers, reset=args.reset)
    logging.info(f"Database initialized with {num_contracts} contracts and {num_customers} customers")


def cmd_scan(args):
    scheduler = ContractMonitorScheduler(args.config)
    result = scheduler.run_daily_scan()
    print("\n=== Daily Scan Result ===")
    for k, v in result.items():
        print(f"  {k}: {v}")


def cmd_reconcile(args):
    scheduler = ContractMonitorScheduler(args.config)
    result = scheduler.run_bank_reconciliation()
    print("\n=== Reconciliation Result ===")
    for k, v in result.items():
        print(f"  {k}: {v}")


def cmd_collection(args):
    scheduler = ContractMonitorScheduler(args.config)
    upcoming = get_upcoming_payment_milestones(
        days_before=scheduler.config.get("collection", {}).get("pre_due_days", 7)
    )
    gen_result = generate_collection_reminders(upcoming, scheduler.config)
    send_result = send_pending_reminders(scheduler.config)
    print(f"\n=== Collection Result ===")
    print(f"  Generated: {gen_result.get('generated', 0)}")
    print(f"  Sent: {send_result.get('sent', 0)}")
    print(f"  Failed: {send_result.get('failed', 0)}")


def cmd_escalate(args):
    scheduler = ContractMonitorScheduler(args.config)
    overdue = get_overdue_payment_milestones(
        min_days=scheduler.config.get("escalation", {}).get("overdue_threshold_days", 15)
    )
    esc_result = process_escalations(overdue, scheduler.config)
    notify_result = notify_escalation_stakeholders(scheduler.config)
    print(f"\n=== Escalation Result ===")
    print(f"  Escalated: {esc_result.get('escalated', 0)}")
    print(f"  Frozen: {esc_result.get('frozen', 0)}")
    print(f"  Notified: {notify_result.get('notified', 0)}")


def cmd_change(args):
    if args.action == "submit":
        import json
        proposed = json.loads(args.proposed)
        result = submit_change_request(args.contract, args.customer, args.type, proposed, args.submitter)
        print(f"\nChange request submitted: {result}")
    elif args.action == "approve":
        result = approve_change(args.flow_id, args.approver, args.comments)
        print(f"\nChange request processed: {result}")
    elif args.action == "reject":
        result = reject_change(args.flow_id, args.approver, args.reason)
        print(f"\nChange request rejected: {result}")
    elif args.action == "list":
        pending = get_pending_approvals(role=args.role)
        print(f"\n=== Pending Approvals ({len(pending)}) ===")
        for p in pending:
            print(f"  Request: {p['request_id']}, Type: {p['change_type']}, "
                  f"Level: {p['current_approver_level']}, Contract: {p['contract_id']}")


def cmd_credit(args):
    if args.action == "update":
        result = update_credit_score(args.customer, args.event)
        print(f"\nCredit score updated: {result}")
    elif args.action == "profile":
        profile = get_customer_credit_profile(args.customer)
        if profile:
            print(f"\n=== Credit Profile: {args.customer} ===")
            c = profile["customer"]
            print(f"  Name: {c['customer_name']}")
            print(f"  Credit Level: {c['credit_level']}")
            print(f"  Credit Score: {c['credit_score']}")
            print(f"  Overdue Count: {c['overdue_count']}")
            print(f"  Order Frozen: {c['order_frozen']}")
    elif args.action == "downgrade":
        scheduler = ContractMonitorScheduler(args.config)
        results = batch_check_downgrades(scheduler.config)
        print(f"\n=== Credit Downgrade Check ===")
        print(f"  Downgraded: {len(results)}")
        for r in results:
            print(f"  Customer: {r['customer_id']}, {r['old_level']}->{r['new_level']}")


def cmd_report(args):
    scheduler = ContractMonitorScheduler(args.config)
    result = scheduler.run_monthly_report(year=args.year, month=args.month)
    print(f"\n=== Monthly Report ===")
    print(f"  Report ID: {result.get('report_id')}")
    print(f"  On-time Rate: {result['stats']['on_time_rate']}%")
    print(f"  Avg Overdue Days: {result['stats']['avg_overdue_days']}")
    print(f"  Bad Debt Rate: {result['stats']['bad_debt_rate']}%")
    print(f"  PDF: {result.get('pdf_path')}")
    print(f"  Excel: {result.get('excel_path')}")


def cmd_logs(args):
    if args.action == "query":
        logs = query_logs(
            contract_id=args.contract, customer_id=args.customer,
            operation_type=args.type, start_date=args.start, end_date=args.end,
            limit=args.limit or 100
        )
        print(f"\n=== Audit Logs ({len(logs)}) ===")
        for l in logs[:50]:
            print(f"  [{l['created_at']}] {l['operation_type']} | "
                  f"Contract: {l['contract_id']} | Customer: {l['customer_id']} | "
                  f"{l['details']}")
    elif args.action == "export":
        path = batch_export_logs(
            output_dir=args.output or "./contract_monitor/reports",
            contract_id=args.contract, customer_id=args.customer,
            start_date=args.start, end_date=args.end,
            format_type=args.format or "excel"
        )
        print(f"\nLogs exported to: {path}")
    elif args.action == "stats":
        stats = get_log_statistics(start_date=args.start, end_date=args.end)
        print(f"\n=== Log Statistics ===")
        for s in stats:
            print(f"  {s['operation_type']}: {s['cnt']}")


def cmd_full(args):
    scheduler = ContractMonitorScheduler(args.config)
    result = scheduler.run_full_daily_workflow()
    print(f"\n=== Full Daily Workflow Result ===")
    print(f"  Scan: {result['scan']}")
    print(f"  Reconciliation: {result['reconciliation']}")
    print(f"  Report: {result.get('report', {}).get('report_id') if result.get('report') else 'N/A'}")
    print(f"  Executed at: {result['executed_at']}")


def cmd_dashboard(args):
    scheduler = ContractMonitorScheduler(args.config)

    print("\n" + "=" * 60)
    print("  CONTRACT FULFILLMENT MONITORING DASHBOARD")
    print("=" * 60)

    contract_count = execute_query(
        "SELECT status, COUNT(*) as cnt FROM contracts GROUP BY status",
        fetch_all=True
    )
    print("\n[Contract Status]")
    for c in contract_count:
        print(f"  {c['status']}: {c['cnt']}")

    milestone_count = execute_query(
        "SELECT status, COUNT(*) as cnt FROM milestones GROUP BY status",
        fetch_all=True
    )
    print("\n[Milestone Status]")
    for m in milestone_count:
        print(f"  {m['status']}: {m['cnt']}")

    risk = get_risk_summary()
    print("\n[Risk Summary]")
    for r in risk.get("risk_levels", []):
        print(f"  {r['risk_level']}: {r['cnt']} customers")
    print(f"  High risk customers: {len(risk.get('high_risk_customers', []))}")

    credit_dist = risk.get("credit_distribution", [])
    print("\n[Credit Distribution]")
    for cd in credit_dist:
        print(f"  Level {cd['credit_level']}: {cd['cnt']} customers (avg score: {cd.get('avg_score', 0):.1f})")

    recon = get_reconciliation_summary()
    print("\n[Bank Matching]")
    for b in recon.get("bank_stats", []):
        status = "Matched" if b["matched"] else "Unmatched"
        print(f"  {status}: {b['cnt']} transactions, ¥{b.get('total', 0):,.2f}")

    pending_approvals = get_pending_approvals()
    print(f"\n[Pending Approvals] {len(pending_approvals)}")

    open_escalations = execute_query(
        "SELECT COUNT(*) as cnt FROM escalation_tickets WHERE status IN ('open', 'notified')",
        fetch_one=True
    )
    print(f"[Open Escalations] {open_escalations['cnt'] if open_escalations else 0}")

    print("\n" + "=" * 60)


def cmd_customers(args):
    scheduler = ContractMonitorScheduler(args.config)

    filters = []
    params = []

    if args.credit_level:
        levels = args.credit_level.split(",")
        placeholders = ",".join(["?"] * len(levels))
        filters.append(f"cu.credit_level IN ({placeholders})")
        params.extend(levels)

    if args.manager:
        filters.append("c.sales_manager = ?")
        params.append(args.manager)

    if args.min_overdue:
        filters.append("overdue_amt >= ?")
        params.append(float(args.min_overdue))

    where_clause = ""
    if filters:
        where_clause = "WHERE " + " AND ".join(filters)

    sql = (
        "SELECT cu.customer_id, cu.customer_name, cu.credit_level, cu.credit_score, "
        "cu.order_frozen, cu.overdue_count, "
        "COUNT(DISTINCT c.contract_id) as contract_count, "
        "COALESCE(SUM(CASE WHEN m.milestone_type = 'payment' "
        "  AND m.status IN ('pending', 'upcoming_due') THEN m.amount ELSE 0 END), 0) as pending_amount, "
        "COALESCE(SUM(CASE WHEN m.milestone_type = 'payment' "
        "  AND m.status = 'overdue' THEN m.amount ELSE 0 END), 0) as overdue_amt "
        "FROM customers cu "
        "LEFT JOIN contracts c ON cu.customer_id = c.customer_id AND c.status = 'active' "
        "LEFT JOIN milestones m ON cu.customer_id = m.customer_id AND m.milestone_type = 'payment' "
        f"{where_clause} "
        "GROUP BY cu.customer_id "
        "ORDER BY overdue_amt DESC"
    )
    customer_rows = execute_query(sql, params, fetch_all=True)

    customer_ids = [r["customer_id"] for r in customer_rows]

    last_reminder_map = {}
    if customer_ids:
        placeholders = ",".join(["?"] * len(customer_ids))
        reminders = execute_query(
            f"SELECT customer_id, MAX(created_at) as last_reminder "
            f"FROM collection_reminders "
            f"WHERE customer_id IN ({placeholders}) "
            f"GROUP BY customer_id",
            customer_ids,
            fetch_all=True
        )
        last_reminder_map = {r["customer_id"]: r["last_reminder"] for r in reminders}

    print("\n" + "=" * 100)
    print("  CUSTOMER OVERVIEW")
    print("=" * 100)

    if args.credit_level:
        print(f"  Filter: Credit Level = {args.credit_level}")
    if args.manager:
        print(f"  Filter: Sales Manager = {args.manager}")
    if args.min_overdue:
        print(f"  Filter: Min Overdue Amount = ¥{float(args.min_overdue):,.2f}")

    print(f"  Total customers: {len(customer_rows)}")
    print()

    header = f"{'ID':<12} {'Customer':<20} {'Credit':<7} {'Score':<6} {'Contracts':<10} " \
             f"{'Pending(¥)':<14} {'Overdue(¥)':<14} {'Frozen':<7} {'Last Reminder':<20}"
    print(header)
    print("-" * 100)

    for r in customer_rows:
        frozen_str = "YES" if r["order_frozen"] else "no"
        last_rem = last_reminder_map.get(r["customer_id"], "-")
        line = f"{r['customer_id']:<12} {r['customer_name']:<20} {r['credit_level']:<7} " \
               f"{r['credit_score']:<6} {r['contract_count']:<10} " \
               f"{r['pending_amount']:>12,.2f} {r['overdue_amt']:>12,.2f} " \
               f"{frozen_str:<7} {last_rem:<20}"
        print(line)

    total_pending = sum(r["pending_amount"] for r in customer_rows)
    total_overdue = sum(r["overdue_amt"] for r in customer_rows)
    frozen_count = sum(1 for r in customer_rows if r["order_frozen"])

    print("-" * 100)
    print(f"{'TOTAL':<12} {'':<20} {'':<7} {'':<6} {len(customer_rows):<10} "
          f"{total_pending:>12,.2f} {total_overdue:>12,.2f} {frozen_count:<7}")
    print("=" * 100)


def main():
    parser = argparse.ArgumentParser(
        description="Contract Fulfillment Monitoring & Intelligent Collection System"
    )
    parser.add_argument("--config", default="config.yaml", help="Config file path")

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    p_init = subparsers.add_parser("init", help="Initialize database with sample data")
    p_init.add_argument("--contracts", type=int, default=500, help="Number of sample contracts")
    p_init.add_argument("--customers", type=int, default=50, help="Number of sample customers")
    p_init.add_argument("--reset", action="store_true", help="Reset database before seeding (clean rebuild)")

    p_scan = subparsers.add_parser("scan", help="Run daily contract scan")
    p_reconcile = subparsers.add_parser("reconcile", help="Run bank reconciliation")
    p_collection = subparsers.add_parser("collection", help="Process collection reminders")
    p_escalate = subparsers.add_parser("escalate", help="Process overdue escalations")

    p_change = subparsers.add_parser("change", help="Manage change requests")
    p_change.add_argument("action", choices=["submit", "approve", "reject", "list"])
    p_change.add_argument("--contract", help="Contract ID")
    p_change.add_argument("--customer", help="Customer ID")
    p_change.add_argument("--type", choices=["extension", "installment"], help="Change type")
    p_change.add_argument("--proposed", help="Proposed data (JSON)")
    p_change.add_argument("--submitter", default="system")
    p_change.add_argument("--flow-id", help="Approval flow ID")
    p_change.add_argument("--approver", help="Approver ID")
    p_change.add_argument("--comments", default="")
    p_change.add_argument("--reason", default="")
    p_change.add_argument("--role", help="Filter by approver role")

    p_credit = subparsers.add_parser("credit", help="Manage credit scores")
    p_credit.add_argument("action", choices=["update", "profile", "downgrade"])
    p_credit.add_argument("--customer", help="Customer ID")
    p_credit.add_argument("--event", choices=["on_time", "minor_late", "major_late"])

    p_report = subparsers.add_parser("report", help="Generate monthly report")
    p_report.add_argument("--year", type=int)
    p_report.add_argument("--month", type=int)

    p_logs = subparsers.add_parser("logs", help="Query and export audit logs")
    p_logs.add_argument("action", choices=["query", "export", "stats"])
    p_logs.add_argument("--contract", help="Filter by contract ID")
    p_logs.add_argument("--customer", help="Filter by customer ID")
    p_logs.add_argument("--type", help="Filter by operation type")
    p_logs.add_argument("--start", help="Start date")
    p_logs.add_argument("--end", help="End date")
    p_logs.add_argument("--limit", type=int, default=100)
    p_logs.add_argument("--output", help="Export output directory")
    p_logs.add_argument("--format", choices=["excel", "csv"], default="excel")

    p_full = subparsers.add_parser("full", help="Run full daily workflow")
    p_dashboard = subparsers.add_parser("dashboard", help="Show monitoring dashboard")

    p_customers = subparsers.add_parser("customers", help="Customer overview with filters")
    p_customers.add_argument("--credit-level", help="Filter by credit level(s), e.g. A,B or C,D")
    p_customers.add_argument("--manager", help="Filter by sales manager name")
    p_customers.add_argument("--min-overdue", help="Filter by minimum overdue amount")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    commands = {
        "init": cmd_init,
        "scan": cmd_scan,
        "reconcile": cmd_reconcile,
        "collection": cmd_collection,
        "escalate": cmd_escalate,
        "change": cmd_change,
        "credit": cmd_credit,
        "report": cmd_report,
        "logs": cmd_logs,
        "full": cmd_full,
        "dashboard": cmd_dashboard,
        "customers": cmd_customers,
    }

    if args.command in commands:
        commands[args.command](args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
