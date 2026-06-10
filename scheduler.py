import os
import yaml
import logging
from datetime import datetime, date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from logging.handlers import RotatingFileHandler

from models import set_db_path, init_db, execute_query
from contract_scanner import scan_active_contracts, get_upcoming_payment_milestones, get_overdue_payment_milestones
from reconciliation import reconcile_erp_deliveries, match_bank_payments
from collection import generate_collection_reminders, send_pending_reminders
from escalation import process_escalations, notify_escalation_stakeholders
from credit_scoring import batch_update_scores_from_payments
from credit_downgrade import batch_check_downgrades
from change_approval import get_pending_approvals
from report import generate_monthly_report
from audit_log import log_operation

logger = logging.getLogger(__name__)


class ContractMonitorScheduler:
    def __init__(self, config_path="config.yaml"):
        self.config = self._load_config(config_path)
        self._setup_logging()
        self._setup_db()

    def _load_config(self, config_path):
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)
            logger.info(f"Configuration loaded from {config_path}")
            return config
        logger.warning(f"Config file {config_path} not found, using defaults")
        return {}

    def _setup_logging(self):
        log_config = self.config.get("logging", {})
        log_level = getattr(logging, log_config.get("level", "INFO").upper(), logging.INFO)
        log_dir = log_config.get("log_dir", "./contract_monitor/logs")
        os.makedirs(log_dir, exist_ok=True)

        root_logger = logging.getLogger()
        root_logger.setLevel(log_level)

        console_handler = logging.StreamHandler()
        console_handler.setLevel(log_level)
        console_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                                        datefmt="%Y-%m-%d %H:%M:%S")
        console_handler.setFormatter(console_fmt)
        root_logger.addHandler(console_handler)

        file_handler = RotatingFileHandler(
            os.path.join(log_dir, "contract_monitor.log"),
            maxBytes=log_config.get("max_file_size_mb", 50) * 1024 * 1024,
            backupCount=log_config.get("backup_count", 10),
            encoding="utf-8"
        )
        file_handler.setLevel(log_level)
        file_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s - %(message)s",
                                     datefmt="%Y-%m-%d %H:%M:%S")
        file_handler.setFormatter(file_fmt)
        root_logger.addHandler(file_handler)

    def _setup_db(self):
        db_path = self.config.get("sqlite", {}).get("db_path", "./contract_monitor.db")
        set_db_path(db_path)
        logger.info(f"Database path set to {db_path}")

    def run_daily_scan(self):
        logger.info("=" * 60)
        logger.info("Starting daily contract scan workflow")
        logger.info("=" * 60)

        scan_result = scan_active_contracts(
            batch_size=self.config.get("scheduler", {}).get("batch_size", 500)
        )
        log_operation("daily_scan", "", "", "system",
                      f"Daily scan result: {scan_result}")

        upcoming = get_upcoming_payment_milestones(
            days_before=self.config.get("collection", {}).get("pre_due_days", 7)
        )
        logger.info(f"Found {len(upcoming)} upcoming payment milestones")

        if upcoming:
            collection_result = generate_collection_reminders(upcoming, self.config)
            send_result = send_pending_reminders(self.config)
            log_operation("collection_processed", "", "", "system",
                          f"Collection: generated={collection_result.get('generated', 0)}, "
                          f"sent={send_result.get('sent', 0)}")

        overdue = get_overdue_payment_milestones(
            min_days=self.config.get("escalation", {}).get("overdue_threshold_days", 15)
        )
        logger.info(f"Found {len(overdue)} overdue milestones exceeding threshold")

        if overdue:
            escalation_result = process_escalations(overdue, self.config)
            notify_result = notify_escalation_stakeholders(self.config)
            log_operation("escalation_processed", "", "", "system",
                          f"Escalation: escalated={escalation_result.get('escalated', 0)}, "
                          f"frozen={escalation_result.get('frozen', 0)}, "
                          f"notified={notify_result.get('notified', 0)}")

        logger.info("Daily contract scan workflow completed")
        return scan_result

    def run_bank_reconciliation(self):
        logger.info("=" * 60)
        logger.info("Starting bank reconciliation workflow")
        logger.info("=" * 60)

        erp_result = reconcile_erp_deliveries()
        bank_result = match_bank_payments(
            tolerance_ratio=0.05
        )

        score_results = batch_update_scores_from_payments(self.config)

        downgrade_results = batch_check_downgrades(self.config)

        log_operation("bank_reconciliation", "", "", "system",
                      f"ERP: {erp_result}, Bank: {bank_result}, "
                      f"Scores updated: {len(score_results)}, "
                      f"Downgrades: {len(downgrade_results)}")

        logger.info("Bank reconciliation workflow completed")
        return {
            "erp": erp_result,
            "bank": bank_result,
            "scores_updated": len(score_results),
            "downgrades": len(downgrade_results)
        }

    def run_monthly_report(self, year=None, month=None):
        logger.info("=" * 60)
        logger.info("Starting monthly report generation")
        logger.info("=" * 60)

        result = generate_monthly_report(year, month, self.config)

        log_operation("monthly_report", "", "", "system",
                      f"Monthly report: {result.get('report_id')}, "
                      f"PDF: {result.get('pdf_path')}, Excel: {result.get('excel_path')}")

        logger.info("Monthly report generation completed")
        return result

    def run_full_daily_workflow(self):
        logger.info("=" * 60)
        logger.info(f"Starting full daily workflow at {datetime.now().isoformat()}")
        logger.info("=" * 60)

        scan_result = self.run_daily_scan()
        reconciliation_result = self.run_bank_reconciliation()

        today = date.today()
        if today.day == self.config.get("scheduler", {}).get("report_day", 1):
            report_result = self.run_monthly_report()
        else:
            report_result = None

        summary = {
            "scan": scan_result,
            "reconciliation": reconciliation_result,
            "report": report_result,
            "executed_at": datetime.now().isoformat()
        }

        log_operation("full_daily_workflow", "", "", "system",
                      f"Full daily workflow completed: {summary}")

        logger.info("=" * 60)
        logger.info("Full daily workflow completed")
        logger.info("=" * 60)

        return summary

    def run_parallel_scan(self, num_workers=None):
        if num_workers is None:
            num_workers = self.config.get("scheduler", {}).get("worker_threads", 16)

        logger.info(f"Starting parallel scan with {num_workers} workers")

        contracts = execute_query(
            "SELECT contract_id FROM contracts WHERE status = 'active'",
            fetch_all=True
        )

        if not contracts:
            logger.info("No active contracts for parallel scan")
            return {"processed": 0}

        batch_size = self.config.get("scheduler", {}).get("batch_size", 500)
        batches = [contracts[i:i + batch_size] for i in range(0, len(contracts), batch_size)]

        processed = 0
        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = []
            for batch in batches:
                future = executor.submit(self._process_contract_batch, batch)
                futures.append(future)

            for future in as_completed(futures):
                try:
                    result = future.result()
                    processed += result
                except Exception as e:
                    logger.error(f"Batch processing failed: {e}")

        logger.info(f"Parallel scan completed: {processed} contracts processed")
        return {"processed": processed}

    def _process_contract_batch(self, contract_batch):
        count = 0
        for c in contract_batch:
            try:
                milestones = execute_query(
                    "SELECT * FROM milestones WHERE contract_id = ?",
                    [c["contract_id"]],
                    fetch_all=True
                )
                count += len(milestones)
            except Exception as e:
                logger.error(f"Error processing contract {c['contract_id']}: {e}")
        return count
