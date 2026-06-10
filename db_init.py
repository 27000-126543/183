import uuid
import random
import logging
from datetime import datetime, timedelta, date
from models import init_db, execute_query, execute_many, execute_update

logger = logging.getLogger(__name__)


def _gen_id(prefix=""):
    return f"{prefix}{uuid.uuid4().hex[:16]}"


def seed_sample_data(num_contracts=200, num_customers=40):
    logger.info(f"Seeding sample data: {num_customers} customers, {num_contracts} contracts")

    customer_names = [
        "华远科技有限公司", "盛达实业集团", "中鼎建设有限公司", "鸿运贸易有限公司",
        "瑞丰电子科技", "嘉禾农业集团", "天成新材料公司", "恒信金融服务",
        "博远医疗器械", "宏图建筑设计", "正通物流集团", "万邦化工实业",
        "银泰零售集团", "金桥教育科技", "飞驰汽车零部件", "绿源环保科技",
        "创新软件技术", "长江船舶工业", "泰山矿业集团", "蓝海船舶工程",
        "旭日光电科技", "大地勘察设计", "华章印刷集团", "锦程人力资源",
        "鹏飞航空部件", "凯旋体育用品", "益民食品加工", "鼎盛重工机械",
        "云帆信息技术", "星辰半导体", "东方生物制药", "汇通投资控股",
        "三江水利集团", "翔宇通信技术", "双鹤药业股份", "振华港口建设",
        "紫金贵金属", "长城安防科技", "福临门粮油集团", "国风文化传媒",
    ]
    credit_levels = ["A", "B", "C", "D"]
    credit_weights = [0.25, 0.40, 0.25, 0.10]

    customers = []
    for i in range(min(num_customers, len(customer_names))):
        cid = f"CUST{i + 1:04d}"
        cl = random.choices(credit_levels, weights=credit_weights, k=1)[0]
        score = {"A": random.randint(90, 100), "B": random.randint(75, 89),
                 "C": random.randint(60, 74), "D": random.randint(0, 59)}[cl]
        customers.append((
            cid, customer_names[i], cl, score,
            random.randint(0, 2), round(random.uniform(0, 300000), 2),
            0, "normal",
            f"contact@{customer_names[i][:2].lower()}.com",
            f"138{random.randint(10000000, 99999999)}",
            datetime.now().isoformat(), datetime.now().isoformat()
        ))

    sql = """INSERT OR IGNORE INTO customers
        (customer_id, customer_name, credit_level, credit_score, overdue_count,
         total_overdue_amount, order_frozen, risk_level, contact_email, contact_phone,
         created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"""
    execute_many(sql, customers)

    managers = ["张经理", "李经理", "王经理", "赵经理", "刘经理", "陈经理", "杨经理", "黄经理"]
    directors = ["周总监", "吴总监", "郑总监", "孙总监"]

    contracts = []
    milestones = []

    for i in range(num_contracts):
        cust = random.choice(customers)
        contract_id = f"CTR{i + 1:06d}"
        contract_no = f"HT-{date.today().year}-{i + 1:05d}"
        sign_date = date.today() - timedelta(days=random.randint(30, 365))
        effective_date = sign_date + timedelta(days=random.randint(0, 7))
        duration = random.randint(90, 730)
        expiry_date = effective_date + timedelta(days=duration)
        total_amount = round(random.uniform(50000, 5000000), 2)

        contracts.append((
            contract_id, contract_no, cust[0], cust[1],
            sign_date.isoformat(), effective_date.isoformat(), expiry_date.isoformat(),
            total_amount, "active", cust[2],
            random.choice(managers), random.choice(directors),
            datetime.now().isoformat(), datetime.now().isoformat()
        ))

        num_payment = random.randint(2, 5)
        num_delivery = random.randint(1, 3)
        pay_per = total_amount / num_payment

        for j in range(num_payment):
            mid = f"MS{len(milestones) + 1:08d}"
            plan_date = effective_date + timedelta(days=int(duration * (j + 1) / (num_payment + 1)))
            actual_date = None
            status = "pending"
            overdue_days = 0

            if plan_date < date.today():
                if random.random() < 0.75:
                    actual_date = (plan_date + timedelta(days=random.randint(-5, 10))).isoformat()
                    status = "completed"
                    if actual_date and plan_date.isoformat() < actual_date:
                        overdue_days = (date.fromisoformat(actual_date) - plan_date).days
                elif random.random() < 0.5:
                    status = "overdue"
                    overdue_days = (date.today() - plan_date).days
            elif (plan_date - date.today()).days <= 7:
                if random.random() < 0.3:
                    status = "upcoming_due"

            milestones.append((
                mid, contract_id, contract_no, cust[0], "payment",
                plan_date.isoformat(), actual_date, round(pay_per, 2),
                status, f"第{j + 1}期付款", overdue_days,
                datetime.now().isoformat(), datetime.now().isoformat()
            ))

        for j in range(num_delivery):
            mid = f"MS{len(milestones) + 1:08d}"
            plan_date = effective_date + timedelta(days=int(duration * (j + 1) / (num_delivery + 1)))
            actual_date = None
            status = "pending"
            overdue_days = 0

            if plan_date < date.today():
                if random.random() < 0.80:
                    actual_date = (plan_date + timedelta(days=random.randint(-3, 7))).isoformat()
                    status = "completed"
                    if actual_date and plan_date.isoformat() < actual_date:
                        overdue_days = (date.fromisoformat(actual_date) - plan_date).days
                else:
                    status = "overdue"
                    overdue_days = (date.today() - plan_date).days
            elif (plan_date - date.today()).days <= 7:
                status = "upcoming_due"

            milestones.append((
                mid, contract_id, contract_no, cust[0], "delivery",
                plan_date.isoformat(), actual_date, 0.0,
                status, f"第{j + 1}批交货", overdue_days,
                datetime.now().isoformat(), datetime.now().isoformat()
            ))

    sql_c = """INSERT OR IGNORE INTO contracts
        (contract_id, contract_no, customer_id, customer_name,
         sign_date, effective_date, expiry_date, total_amount,
         status, credit_level, sales_manager, sales_director,
         created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    execute_many(sql_c, contracts)

    sql_m = """INSERT OR IGNORE INTO milestones
        (milestone_id, contract_id, contract_no, customer_id, milestone_type,
         planned_date, actual_date, amount, status, description, overdue_days,
         created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    execute_many(sql_m, milestones)

    bank_txns = []
    for i in range(num_contracts * 3):
        cust = random.choice(customers)
        txn_id = f"TXN{i + 1:08d}"
        txn_date = date.today() - timedelta(days=random.randint(0, 90))
        amount = round(random.uniform(10000, 2000000), 2)
        bank_txns.append((
            txn_id, f"BNK{random.randint(100000, 999999)}", cust[0],
            amount, txn_date.isoformat(), 0, None,
            datetime.now().isoformat()
        ))

    sql_b = """INSERT OR IGNORE INTO bank_transactions
        (transaction_id, bank_ref, customer_id, amount, transaction_date,
         matched, matched_milestone_id, created_at)
        VALUES (?,?,?,?,?,?,?,?)"""
    execute_many(sql_b, bank_txns)

    erp_records = []
    for i in range(num_contracts * 2):
        c = random.choice(contracts)
        rec_id = f"ERP{i + 1:08d}"
        del_date = date.today() - timedelta(days=random.randint(0, 120))
        status = random.choice(["pending", "delivered", "partial"])
        erp_records.append((
            rec_id, f"ERP-REF-{random.randint(10000, 99999)}", c[0],
            status, del_date.isoformat() if status != "pending" else None,
            round(random.uniform(100, 10000), 2), f"ERP交货记录{i + 1}",
            datetime.now().isoformat()
        ))

    sql_e = """INSERT OR IGNORE INTO erp_records
        (record_id, erp_ref, contract_id, delivery_status, delivery_date,
         quantity, description, created_at)
        VALUES (?,?,?,?,?,?,?,?)"""
    execute_many(sql_e, erp_records)

    logger.info(f"Sample data seeded: {len(customers)} customers, {len(contracts)} contracts, "
                f"{len(milestones)} milestones, {len(bank_txns)} bank txns, {len(erp_records)} ERP records")


def init_and_seed(num_contracts=200, num_customers=40):
    init_db()
    seed_sample_data(num_contracts, num_customers)
    logger.info("Database initialization and seeding complete")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_and_seed(500, 50)
