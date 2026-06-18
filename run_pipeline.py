"""
Full pipeline orchestrator — runs all three steps in order:

  Step 1: Incremental user refresh  (run_production_users_by_centre.py)
  Step 2: User addon attributes      (run_user_addon.py)
  Step 3: Cleanup inactive records   (run_cleanup_inactive.py)
  Step 4: SQL filter table           (run_sql_filters.py → sql_ael_filters)

All output is written to logs/pipeline_YYYY-MM-DD_HH-MM-SS.log and
optionally emailed to PIPELINE_EMAIL_TO on completion (pass or fail).

Usage:
    python3 run_pipeline.py
    python3 run_pipeline.py --workers 8
    python3 run_pipeline.py --dry-run          # cleanup step only previews
    python3 run_pipeline.py --no-email         # skip email even if configured
    python3 run_pipeline.py --target-table production_users_one_record

Email setup — add to .env:
    PIPELINE_EMAIL_SMTP_HOST=smtp.gmail.com
    PIPELINE_EMAIL_SMTP_PORT=587
    PIPELINE_EMAIL_SMTP_USER=analytics@questalliance.net
    PIPELINE_EMAIL_SMTP_PASSWORD=your_app_password
    PIPELINE_EMAIL_TO=joseph@questalliance.net
"""

import argparse
import datetime
import logging
import os
import smtplib
import subprocess
import sys
import threading
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import pymysql
import psutil
from dotenv import load_dotenv

load_dotenv()

from config import ANALYTICS_DB
from db import fetch

LOGS_DIR = Path("logs")
LOGS_DIR.mkdir(exist_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# System monitor — background thread logs CPU + RAM on a fixed interval
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def log_system_stats(label: str = "") -> None:
    cpu = psutil.cpu_percent(interval=1)
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()
    tag = f" [{label}]" if label else ""
    logging.info(
        "[SYSTEM%s] CPU: %.1f%%  |  RAM: %s / %s (%.1f%% used)  |  Swap: %s / %s (%.1f%% used)",
        tag,
        cpu,
        _fmt_bytes(mem.used), _fmt_bytes(mem.total), mem.percent,
        _fmt_bytes(swap.used), _fmt_bytes(swap.total), swap.percent,
    )


def _monitor_loop(stop_event: threading.Event, interval: int) -> None:
    while not stop_event.wait(interval):
        log_system_stats()


def start_monitor(interval: int = 30) -> threading.Event:
    """Start background system monitor. Returns the stop event to call .set() on."""
    stop_event = threading.Event()
    t = threading.Thread(target=_monitor_loop, args=(stop_event, interval), daemon=True)
    t.start()
    return stop_event


# ─────────────────────────────────────────────────────────────────────────────
# Logging: console + file simultaneously
# ─────────────────────────────────────────────────────────────────────────────

def setup_logging(log_path: Path) -> None:
    log_format = "%(asctime)s %(levelname)-8s %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter(log_format, date_format))
    root.addHandler(console)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(log_format, date_format))
    root.addHandler(file_handler)


# ─────────────────────────────────────────────────────────────────────────────
# Subprocess runner — streams output line-by-line to the logger
# ─────────────────────────────────────────────────────────────────────────────

def run_step(label: str, cmd: list[str]) -> bool:
    """Run a command, stream its output through the logger. Returns True on success."""
    logging.info("=" * 70)
    logging.info("STEP START: %s", label)
    logging.info("Command:    %s", " ".join(cmd))
    logging.info("=" * 70)

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    for line in proc.stdout:
        logging.info("[%s] %s", label, line.rstrip())

    proc.wait()
    success = proc.returncode == 0

    if success:
        logging.info("STEP OK:    %s (exit 0)", label)
    else:
        logging.error("STEP FAILED: %s (exit %d)", label, proc.returncode)

    return success


# ─────────────────────────────────────────────────────────────────────────────
# Email sender
# ─────────────────────────────────────────────────────────────────────────────

def send_email(subject: str, log_path: Path) -> None:
    smtp_host = os.getenv("PIPELINE_EMAIL_SMTP_HOST", "")
    smtp_port = int(os.getenv("PIPELINE_EMAIL_SMTP_PORT", "587"))
    smtp_user = os.getenv("PIPELINE_EMAIL_SMTP_USER", "")
    smtp_password = os.getenv("PIPELINE_EMAIL_SMTP_PASSWORD", "")
    email_to = os.getenv("PIPELINE_EMAIL_TO", "")

    if not all([smtp_host, smtp_user, smtp_password, email_to]):
        logging.warning(
            "Email not configured — set PIPELINE_EMAIL_SMTP_HOST, "
            "PIPELINE_EMAIL_SMTP_USER, PIPELINE_EMAIL_SMTP_PASSWORD, "
            "PIPELINE_EMAIL_TO in .env to enable email reports."
        )
        return

    log_text = log_path.read_text(encoding="utf-8")

    msg = MIMEMultipart()
    msg["From"] = smtp_user
    msg["To"] = email_to
    msg["Subject"] = subject

    # Plain-text body: last 100 lines of the log (summary)
    tail = "\n".join(log_text.splitlines()[-100:])
    body = (
        f"Pipeline log summary (last 100 lines):\n\n{tail}\n\n"
        f"Full log attached: {log_path.name}"
    )
    msg.attach(MIMEText(body, "plain"))

    # Full log as attachment
    msg.attach(MIMEText(log_text, "plain", "utf-8"))
    msg.get_payload()[-1].add_header(
        "Content-Disposition", "attachment", filename=log_path.name
    )

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_user, email_to, msg.as_string())
        logging.info("Email sent to %s", email_to)
    except Exception as exc:
        logging.error("Failed to send email: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def _target_table_exists(table_name: str) -> bool:
    """Return True if the table already exists in the analytics DB."""
    try:
        fetch(ANALYTICS_DB, f"SELECT 1 FROM `{table_name}` LIMIT 0")
        return True
    except pymysql.err.ProgrammingError as exc:
        if exc.args[0] == 1146:
            return False
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the full AEL pipeline in order.")
    parser.add_argument(
        "--target-table",
        default="production_users_one_record",
        help="Analytics table name used across all steps. Default: production_users_one_record",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Parallel workers for the incremental refresh step. Default: 4",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Pass --dry-run to the cleanup step (preview deletes without applying them).",
    )
    parser.add_argument(
        "--no-email",
        action="store_true",
        help="Skip sending the email report even if SMTP is configured.",
    )
    parser.add_argument(
        "--monitor-interval",
        type=int,
        default=30,
        help="Seconds between system resource log lines. Default: 30",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_path = LOGS_DIR / f"pipeline_{timestamp}.log"

    setup_logging(log_path)
    logging.info("Pipeline started — log file: %s", log_path)
    logging.info("Target table : %s", args.target_table)
    logging.info("Workers      : %d", args.workers)
    logging.info("Dry run      : %s", args.dry_run)
    logging.info("Monitor      : every %ds", args.monitor_interval)

    log_system_stats("startup")
    stop_monitor = start_monitor(args.monitor_interval)

    python = sys.executable
    results: dict[str, bool] = {}

    # ── Step 1: Full (centre-based) or incremental (user-based) refresh ────────
    table_exists = _target_table_exists(args.target_table)
    if table_exists:
        logging.info("Table '%s' exists — running incremental refresh by user", args.target_table)
        step1_label = "1. Incremental refresh"
        step1_cmd = [
            python, "run_production_users_by_centre.py",
            "--target-table", args.target_table,
            "--workers", str(args.workers),
            "--incremental-users",
        ]
    else:
        logging.info(
            "Table '%s' not found — running full centre refresh for first-time populate",
            args.target_table,
        )
        step1_label = "1. Full centre refresh (first run)"
        step1_cmd = [
            python, "run_production_users_by_centre.py",
            "--target-table", args.target_table,
            "--workers", str(args.workers),
            "--centre-sql-path", "sql_queries/centre_ids.sql",
        ]
    results[step1_label] = run_step(step1_label, step1_cmd)
    log_system_stats("after step 1")

    # ── Step 2: User addon attributes ───────────────────────────────────────
    step2_cmd = [
        python, "run_user_addon.py",
        "--target-table", "user_addon",
    ]
    results["2. User addon"] = run_step("2. User addon", step2_cmd)
    log_system_stats("after step 2")

    # ── Step 3: Cleanup inactive users / centres ─────────────────────────────
    step3_cmd = [
        python, "run_cleanup_inactive.py",
        "--target-table", args.target_table,
    ]
    if args.dry_run:
        step3_cmd.append("--dry-run")
    results["3. Cleanup inactive"] = run_step("3. Cleanup inactive", step3_cmd)
    log_system_stats("after step 3")

    # ── Step 4: SQL filter table (sql_ael_filters) ───────────────────────────
    step4_cmd = [
        python, "run_sql_filters.py",
        "--source-table", args.target_table,
        "--target-table", "sql_ael_filters",
    ]
    results["4. SQL filter table"] = run_step("4. SQL filter table", step4_cmd)

    stop_monitor.set()

    # ── Summary ──────────────────────────────────────────────────────────────
    logging.info("=" * 70)
    logging.info("PIPELINE SUMMARY")
    logging.info("=" * 70)
    all_passed = True
    for step, passed in results.items():
        status = "PASS" if passed else "FAIL"
        logging.info("  %s  %s", status, step)
        if not passed:
            all_passed = False

    overall = "SUCCESS" if all_passed else "FAILED"
    logging.info("Overall: %s", overall)
    log_system_stats("shutdown")
    logging.info("Log saved to: %s", log_path)

    # ── Email report ─────────────────────────────────────────────────────────
    if not args.no_email:
        subject = f"[AEL Pipeline] {overall} — {timestamp}"
        send_email(subject, log_path)

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
