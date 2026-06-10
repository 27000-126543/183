import sqlite3
import json
import os
import logging
from datetime import datetime, date
from contextlib import contextmanager

logger = logging.getLogger(__name__)

DB_PATH = "./contract_monitor.db"


def set_db_path(path):
    global DB_PATH
    DB_PATH = path


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS customers (
    customer_id TEXT PRIMARY KEY,
    customer_name TEXT NOT NULL,
    credit_level TEXT NOT NULL DEFAULT 'B',
    credit_score INTEGER NOT NULL DEFAULT 80,
    overdue_count INTEGER NOT NULL DEFAULT 0,
    total_overdue_amount REAL NOT NULL DEFAULT 0.0,
    order_frozen INTEGER NOT NULL DEFAULT 0,
    risk_level TEXT NOT NULL DEFAULT 'normal',
    contact_email TEXT DEFAULT '',
    contact_phone TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS contracts (
    contract_id TEXT PRIMARY KEY,
    contract_no TEXT NOT NULL UNIQUE,
    customer_id TEXT NOT NULL,
    customer_name TEXT NOT NULL,
    sign_date TEXT NOT NULL,
    effective_date TEXT NOT NULL,
    expiry_date TEXT NOT NULL,
    total_amount REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    credit_level TEXT NOT NULL DEFAULT 'B',
    sales_manager TEXT DEFAULT '',
    sales_director TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
);

CREATE TABLE IF NOT EXISTS milestones (
    milestone_id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL,
    contract_no TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    milestone_type TEXT NOT NULL,
    planned_date TEXT NOT NULL,
    actual_date TEXT,
    amount REAL NOT NULL DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'pending',
    description TEXT DEFAULT '',
    overdue_days INTEGER DEFAULT 0,
    credit_scored INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (contract_id) REFERENCES contracts(contract_id)
);

CREATE TABLE IF NOT EXISTS bank_transactions (
    transaction_id TEXT PRIMARY KEY,
    bank_ref TEXT,
    customer_id TEXT NOT NULL,
    amount REAL NOT NULL,
    transaction_date TEXT NOT NULL,
    matched INTEGER NOT NULL DEFAULT 0,
    matched_milestone_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS erp_records (
    record_id TEXT PRIMARY KEY,
    erp_ref TEXT,
    contract_id TEXT NOT NULL,
    delivery_status TEXT NOT NULL DEFAULT 'pending',
    delivery_date TEXT,
    quantity REAL DEFAULT 0.0,
    description TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (contract_id) REFERENCES contracts(contract_id)
);

CREATE TABLE IF NOT EXISTS collection_reminders (
    reminder_id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    milestone_id TEXT NOT NULL,
    reminder_type TEXT NOT NULL,
    content TEXT DEFAULT '',
    recipient TEXT DEFAULT '',
    sent_date TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS escalation_tickets (
    ticket_id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    milestone_id TEXT NOT NULL,
    escalation_level INTEGER NOT NULL DEFAULT 1,
    notified_roles TEXT DEFAULT '[]',
    freeze_action INTEGER DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS change_requests (
    request_id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    change_type TEXT NOT NULL,
    original_data TEXT DEFAULT '{}',
    proposed_data TEXT DEFAULT '{}',
    approval_status TEXT NOT NULL DEFAULT 'pending',
    current_approver_level INTEGER DEFAULT 1,
    created_by TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    approved_at TEXT,
    rejected_at TEXT,
    rejection_reason TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS approval_flows (
    flow_id TEXT PRIMARY KEY,
    change_request_id TEXT NOT NULL,
    approver_level INTEGER NOT NULL,
    approver_role TEXT NOT NULL,
    approver_id TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    comments TEXT DEFAULT '',
    action_date TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (change_request_id) REFERENCES change_requests(request_id)
);

CREATE TABLE IF NOT EXISTS credit_history (
    history_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    old_level TEXT NOT NULL,
    new_level TEXT NOT NULL,
    old_score INTEGER NOT NULL,
    new_score INTEGER NOT NULL,
    reason TEXT DEFAULT '',
    change_date TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS monthly_reports (
    report_id TEXT PRIMARY KEY,
    report_month TEXT NOT NULL,
    on_time_rate REAL DEFAULT 0.0,
    avg_overdue_days REAL DEFAULT 0.0,
    bad_debt_rate REAL DEFAULT 0.0,
    credit_distribution TEXT DEFAULT '{}',
    generated_date TEXT,
    pdf_path TEXT,
    excel_path TEXT
);

CREATE TABLE IF NOT EXISTS audit_logs (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_type TEXT NOT NULL,
    contract_id TEXT DEFAULT '',
    customer_id TEXT DEFAULT '',
    operator TEXT DEFAULT 'system',
    details TEXT DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_contracts_status ON contracts(status);
CREATE INDEX IF NOT EXISTS idx_contracts_customer ON contracts(customer_id);
CREATE INDEX IF NOT EXISTS idx_milestones_contract ON milestones(contract_id);
CREATE INDEX IF NOT EXISTS idx_milestones_status ON milestones(status);
CREATE INDEX IF NOT EXISTS idx_milestones_planned_date ON milestones(planned_date);
CREATE INDEX IF NOT EXISTS idx_milestones_type_status ON milestones(milestone_type, status);
CREATE INDEX IF NOT EXISTS idx_bank_customer ON bank_transactions(customer_id);
CREATE INDEX IF NOT EXISTS idx_bank_matched ON bank_transactions(matched);
CREATE INDEX IF NOT EXISTS idx_bank_date ON bank_transactions(transaction_date);
CREATE INDEX IF NOT EXISTS idx_erp_contract ON erp_records(contract_id);
CREATE INDEX IF NOT EXISTS idx_reminders_status ON collection_reminders(status);
CREATE INDEX IF NOT EXISTS idx_escalation_status ON escalation_tickets(status);
CREATE INDEX IF NOT EXISTS idx_change_status ON change_requests(approval_status);
CREATE INDEX IF NOT EXISTS idx_approval_request ON approval_flows(change_request_id);
CREATE INDEX IF NOT EXISTS idx_credit_customer ON credit_history(customer_id);
CREATE INDEX IF NOT EXISTS idx_audit_type ON audit_logs(operation_type);
CREATE INDEX IF NOT EXISTS idx_audit_contract ON audit_logs(contract_id);
CREATE INDEX IF NOT EXISTS idx_audit_customer ON audit_logs(customer_id);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at);
"""


@contextmanager
def get_connection():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_connection() as conn:
        conn.executescript(SCHEMA_SQL)
    logger.info("Database initialized successfully")


def row_to_dict(row):
    if row is None:
        return None
    d = dict(row)
    for key, value in d.items():
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, (dict, list)):
                    d[key] = parsed
            except (json.JSONDecodeError, ValueError):
                pass
    return d


def execute_query(sql, params=None, fetch_one=False, fetch_all=False):
    with get_connection() as conn:
        cursor = conn.execute(sql, params or [])
        if fetch_one:
            row = cursor.fetchone()
            return row_to_dict(row)
        if fetch_all:
            rows = cursor.fetchall()
            return [row_to_dict(r) for r in rows]
        return cursor.lastrowid


def execute_many(sql, params_list):
    with get_connection() as conn:
        conn.executemany(sql, params_list)
    return len(params_list)


def execute_update(sql, params=None):
    with get_connection() as conn:
        cursor = conn.execute(sql, params or [])
        return cursor.rowcount
