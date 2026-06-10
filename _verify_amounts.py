#!/usr/bin/env python3
import sys
sys.path.insert(0, '.')
from models import execute_query

sd = '2026-06-01'
ed = '2026-07-01'

cpa = execute_query(
    "SELECT SUM(month_planned) as tp, SUM(month_received) as tr, SUM(month_overdue) as to2 "
    "FROM ("
    "  SELECT m.customer_id, "
    "  SUM(CASE WHEN m.planned_date >= ? AND m.planned_date < ? THEN m.amount ELSE 0 END) as month_planned, "
    "  SUM(CASE WHEN m.actual_date >= ? AND m.actual_date < ? AND m.status = 'completed' THEN m.amount ELSE 0 END) as month_received, "
    "  SUM(CASE WHEN m.planned_date >= ? AND m.planned_date < ? AND m.status = 'overdue' THEN m.amount ELSE 0 END) as month_overdue "
    "  FROM milestones m WHERE m.milestone_type = 'payment' "
    "  GROUP BY m.customer_id"
    ")",
    [sd, ed, sd, ed, sd, ed],
    fetch_one=True
)
print("Customer Payment Analysis (subquery, no inflation):")
print(f"  Total planned: {cpa['tp']:,.0f}")
print(f"  Total received: {cpa['tr']:,.0f}")
print(f"  Total overdue: {cpa['to2']:,.0f}")

direct = execute_query(
    "SELECT "
    "SUM(CASE WHEN planned_date >= ? AND planned_date < ? THEN amount ELSE 0 END) as tp, "
    "SUM(CASE WHEN actual_date >= ? AND actual_date < ? AND status = 'completed' THEN amount ELSE 0 END) as tr, "
    "SUM(CASE WHEN planned_date >= ? AND planned_date < ? AND status = 'overdue' THEN amount ELSE 0 END) as to2 "
    "FROM milestones WHERE milestone_type = 'payment'",
    [sd, ed, sd, ed, sd, ed],
    fetch_one=True
)
print("\nDirect milestone query:")
print(f"  Total planned: {direct['tp']:,.0f}")
print(f"  Total received: {direct['tr']:,.0f}")
print(f"  Total overdue: {direct['to2']:,.0f}")

if abs(cpa['tp'] - direct['tp']) < 1 and abs(cpa['tr'] - direct['tr']) < 1 and abs(cpa['to2'] - direct['to2']) < 1:
    print("\n✅ Amounts match! No inflation from JOINs.")
else:
    print("\n❌ MISMATCH! Amounts differ between subquery and direct query.")
