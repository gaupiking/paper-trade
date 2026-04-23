import csv
import sqlite3
import datetime as dt
from pathlib import Path
import streamlit as st

DB_PATH = "paper_trade.db"

INITIAL_CASH = 200_000_000
MAX_SINGLE_COST = 40_000_000
MIN_TOTAL_HOLDING_COST = 20_000_000
MAX_PROJECT_LOSS = 20_000_000
MAX_MONTH_LOSS = 10_000_000
MAX_SINGLE_LOSS_RATIO = 0.30
FEE_RATE = 0.0004
JUNE_RESET_DATE = dt.date(2026, 7, 1)

TEAM_TYPES = {
    "股票投資組": "STOCK",
    "ETF投資組": "ETF",
}

SYSTEM_ROLE = "SYSTEM"


def connect():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def parse_date(s: str):
    return dt.datetime.strptime(s, "%Y-%m-%d").date()


def parse_dt(s: str):
    return dt.datetime.strptime(s, "%Y-%m-%d %H:%M")


def init_db(conn):
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS teams (
            team_name TEXT PRIMARY KEY,
            group_type TEXT NOT NULL,
            initial_cash REAL NOT NULL,
            suspended_until TEXT,
            hard_stop INTEGER DEFAULT 0,
            hard_stop_reason TEXT
        );

        CREATE TABLE IF NOT EXISTS securities (
            symbol TEXT PRIMARY KEY,
            name TEXT,
            asset_class TEXT NOT NULL,
            etf_subtype TEXT,
            liquidity_ok INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS prices (
            trade_date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            close_price REAL NOT NULL,
            PRIMARY KEY (trade_date, symbol)
        );

        CREATE TABLE IF NOT EXISTS orders (
            order_id INTEGER PRIMARY KEY AUTOINCREMENT,
            team_name TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            submitted_at TEXT NOT NULL,
            effective_trade_date TEXT NOT NULL,
            status TEXT NOT NULL,
            reason TEXT,
            allocation_reason TEXT,
            ai_used INTEGER DEFAULT 0,
            ai_timing TEXT,
            created_by_role TEXT,
            created_by_name TEXT,
            rejection_reason TEXT
        );

        CREATE TABLE IF NOT EXISTS executions (
            exec_id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            team_name TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            exec_date TEXT NOT NULL,
            exec_price REAL NOT NULL,
            quantity INTEGER NOT NULL,
            gross_amount REAL NOT NULL,
            fee REAL NOT NULL,
            tax REAL NOT NULL,
            cash_flow REAL NOT NULL,
            realized_pnl REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS positions (
            team_name TEXT NOT NULL,
            symbol TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            avg_cost REAL NOT NULL,
            total_cost REAL NOT NULL,
            PRIMARY KEY (team_name, symbol)
        );

        CREATE TABLE IF NOT EXISTS journals (
            journal_id INTEGER PRIMARY KEY AUTOINCREMENT,
            team_name TEXT NOT NULL,
            journal_date TEXT NOT NULL,
            entry_type TEXT NOT NULL,
            content TEXT NOT NULL,
            ai_used INTEGER DEFAULT 0,
            ai_timing TEXT,
            created_by_role TEXT,
            created_by_name TEXT
        );

        CREATE TABLE IF NOT EXISTS alerts (
            alert_id INTEGER PRIMARY KEY AUTOINCREMENT,
            team_name TEXT NOT NULL,
            alert_date TEXT NOT NULL,
            severity TEXT NOT NULL,
            alert_type TEXT NOT NULL,
            message TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS daily_nav (
            team_name TEXT NOT NULL,
            nav_date TEXT NOT NULL,
            cash REAL NOT NULL,
            holdings_cost REAL NOT NULL,
            market_value REAL NOT NULL,
            unrealized_pnl REAL NOT NULL,
            realized_pnl REAL NOT NULL,
            total_pnl REAL NOT NULL,
            nav REAL NOT NULL,
            project_pnl REAL NOT NULL,
            june_pnl REAL NOT NULL,
            july_pnl REAL NOT NULL,
            max_drawdown REAL NOT NULL,
            PRIMARY KEY (team_name, nav_date)
        );

        CREATE TABLE IF NOT EXISTS forced_actions (
            action_id INTEGER PRIMARY KEY AUTOINCREMENT,
            team_name TEXT NOT NULL,
            action_date TEXT NOT NULL,
            action_type TEXT NOT NULL,
            symbol TEXT,
            reason TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING'
        );
        """
    )

    for team_name, group_type in TEAM_TYPES.items():
        cur.execute(
            """
            INSERT OR IGNORE INTO teams (team_name, group_type, initial_cash)
            VALUES (?, ?, ?)
            """,
            (team_name, group_type, INITIAL_CASH),
        )

    conn.commit()


def get_team(conn, team_name):
    row = conn.execute(
        "SELECT * FROM teams WHERE team_name=?",
        (team_name,)
    ).fetchone()
    if not row:
        raise ValueError(f"找不到隊伍：{team_name}")
    return row


def get_security(conn, symbol):
    row = conn.execute(
        "SELECT * FROM securities WHERE symbol=?",
        (symbol,)
    ).fetchone()
    if not row:
        raise ValueError(f"找不到標的：{symbol}")
    return row


def get_position(conn, team_name, symbol):
    return conn.execute(
        "SELECT * FROM positions WHERE team_name=? AND symbol=?",
        (team_name, symbol)
    ).fetchone()


def get_trade_dates(conn):
    rows = conn.execute(
        "SELECT DISTINCT trade_date FROM prices ORDER BY trade_date"
    ).fetchall()
    return [parse_date(r["trade_date"]) for r in rows]


def get_next_trade_date(conn, trade_date):
    for d in get_trade_dates(conn):
        if d > trade_date:
            return d
    raise ValueError("找不到下一個交易日，請先匯入足夠價格資料")


def get_close_price(conn, trade_date, symbol):
    row = conn.execute(
        "SELECT close_price FROM prices WHERE trade_date=? AND symbol=?",
        (trade_date.isoformat(), symbol)
    ).fetchone()
    if not row:
        raise ValueError(f"缺少價格資料：{trade_date} {symbol}")
    return float(row["close_price"])


def tax_rate(sec):
    if sec["asset_class"] == "STOCK":
        return 0.003
    if sec["asset_class"] == "ETF":
        if (sec["etf_subtype"] or "").upper() == "BOND":
            return 0.0
        return 0.001
    return 0.0


def current_cash(conn, team_name):
    team = get_team(conn, team_name)
    row = conn.execute(
        "SELECT COALESCE(SUM(cash_flow), 0) AS x FROM executions WHERE team_name=?",
        (team_name,)
    ).fetchone()
    return float(team["initial_cash"]) + float(row["x"])


def holding_cost(conn, team_name):
    row = conn.execute(
        "SELECT COALESCE(SUM(total_cost), 0) AS x FROM positions WHERE team_name=? AND quantity>0",
        (team_name,)
    ).fetchone()
    return float(row["x"])


def market_value(conn, team_name, nav_date):
    rows = conn.execute(
        "SELECT symbol, quantity FROM positions WHERE team_name=? AND quantity>0",
        (team_name,)
    ).fetchall()
    total = 0.0
    for r in rows:
        total += int(r["quantity"]) * get_close_price(conn, nav_date, r["symbol"])
    return total


def total_realized_pnl(conn, team_name):
    row = conn.execute(
        "SELECT COALESCE(SUM(realized_pnl), 0) AS x FROM executions WHERE team_name=?",
        (team_name,)
    ).fetchone()
    return float(row["x"])


def nav_on(conn, team_name, nav_date, default=INITIAL_CASH):
    row = conn.execute(
        "SELECT nav FROM daily_nav WHERE team_name=? AND nav_date=?",
        (team_name, nav_date.isoformat())
    ).fetchone()
    return float(row["nav"]) if row else float(default)


def calculate_mdd(conn, team_name, nav_date, current_nav):
    rows = conn.execute(
        "SELECT nav FROM daily_nav WHERE team_name=? AND nav_date<? ORDER BY nav_date",
        (team_name, nav_date.isoformat())
    ).fetchall()
    navs = [float(r["nav"]) for r in rows] + [current_nav]
    if not navs:
        return 0.0
    peak = navs[0]
    mdd = 0.0
    for x in navs:
        peak = max(peak, x)
        mdd = max(mdd, peak - x)
    return mdd


def update_daily_nav(conn, team_name, nav_date):
    cash = current_cash(conn, team_name)
    cost = holding_cost(conn, team_name)
    mv = market_value(conn, team_name, nav_date)
    realized = total_realized_pnl(conn, team_name)
    unrealized = mv - cost
    nav = cash + mv
    total_pnl = nav - INITIAL_CASH

    if nav_date < JUNE_RESET_DATE:
        june_pnl = total_pnl
        july_pnl = 0.0
    else:
        june_end_nav = nav_on(conn, team_name, dt.date(2026, 6, 30), INITIAL_CASH)
        june_pnl = june_end_nav - INITIAL_CASH
        july_pnl = nav - june_end_nav

    mdd = calculate_mdd(conn, team_name, nav_date, nav)

    conn.execute(
        """
        INSERT OR REPLACE INTO daily_nav (
            team_name, nav_date, cash, holdings_cost, market_value,
            unrealized_pnl, realized_pnl, total_pnl, nav, project_pnl,
            june_pnl, july_pnl, max_drawdown
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            team_name, nav_date.isoformat(), cash, cost, mv,
            unrealized, realized, total_pnl, nav, total_pnl,
            june_pnl, july_pnl, mdd
        )
    )
    conn.commit()


def add_security(conn, symbol, name, asset_class, etf_subtype="", liquidity_ok=1):
    conn.execute(
        """
        INSERT OR REPLACE INTO securities
        (symbol, name, asset_class, etf_subtype, liquidity_ok)
        VALUES (?, ?, ?, ?, ?)
        """,
        (symbol, name, asset_class.upper(), etf_subtype.upper() if etf_subtype else None, int(liquidity_ok))
    )
    conn.commit()


def import_securities_csv(conn, csv_path):
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            add_security(
                conn,
                row["symbol"],
                row.get("name", ""),
                row["asset_class"],
                row.get("etf_subtype", ""),
                row.get("liquidity_ok", "1"),
            )


def import_prices_csv(conn, csv_path):
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            conn.execute(
                """
                INSERT OR REPLACE INTO prices (trade_date, symbol, close_price)
                VALUES (?, ?, ?)
                """,
                (row["trade_date"], row["symbol"], float(row["close_price"]))
            )
    conn.commit()


def place_order(
    conn, team_name, symbol, side, quantity, submitted_at,
    reason="", allocation_reason="", ai_used=False, ai_timing="",
    created_by_role="", created_by_name=""
):
    team = get_team(conn, team_name)
    sec = get_security(conn, symbol)

    if int(sec["liquidity_ok"]) != 1:
        raise ValueError(f"{symbol} 被標記為流動性不佳，不可交易")

    if team["group_type"] != sec["asset_class"]:
        raise ValueError(f"{team_name} 不可交易 {sec['asset_class']} 標的")

    if int(team["hard_stop"]) == 1:
        raise ValueError(f"{team_name} 已停止交易：{team['hard_stop_reason']}")

    submitted_dt = parse_dt(submitted_at)
    if team["suspended_until"]:
        if submitted_dt.date() < parse_date(team["suspended_until"]):
            raise ValueError(f"{team_name} 停權至 {team['suspended_until']}")

    cutoff = submitted_dt.replace(hour=13, minute=30, second=0, microsecond=0)
    if submitted_dt <= cutoff:
        effective_trade_date = submitted_dt.date()
    else:
        effective_trade_date = get_next_trade_date(conn, submitted_dt.date())

    conn.execute(
        """
        INSERT INTO orders (
            team_name, symbol, side, quantity, submitted_at,
            effective_trade_date, status, reason, allocation_reason,
            ai_used, ai_timing, created_by_role, created_by_name
        ) VALUES (?, ?, ?, ?, ?, ?, 'PENDING', ?, ?, ?, ?, ?, ?)
        """,
        (
            team_name, symbol, side.upper(), int(quantity), submitted_at,
            effective_trade_date.isoformat(), reason, allocation_reason,
            int(ai_used), ai_timing, created_by_role, created_by_name
        )
    )
    conn.commit()


def import_orders_csv(conn, csv_path):
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            place_order(
                conn,
                team_name=row["team_name"],
                symbol=row["symbol"],
                side=row["side"],
                quantity=int(row["quantity"]),
                submitted_at=row["submitted_at"],
                reason=row.get("reason", ""),
                allocation_reason=row.get("allocation_reason", ""),
                ai_used=row.get("ai_used", "0") in ("1", "true", "True"),
                ai_timing=row.get("ai_timing", ""),
                created_by_role=row.get("created_by_role", ""),
                created_by_name=row.get("created_by_name", ""),
            )


def add_journal(
    conn, team_name, journal_date, entry_type, content,
    ai_used=False, ai_timing="", created_by_role="", created_by_name=""
):
    conn.execute(
        """
        INSERT INTO journals (
            team_name, journal_date, entry_type, content,
            ai_used, ai_timing, created_by_role, created_by_name
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            team_name, journal_date, entry_type, content,
            int(ai_used), ai_timing, created_by_role, created_by_name
        )
    )
    conn.commit()


def reject_order(conn, order_id, reason):
    conn.execute(
        "UPDATE orders SET status='REJECTED', rejection_reason=? WHERE order_id=?",
        (reason, order_id)
    )
    conn.commit()


def order_allowed_to_execute(conn, order, trade_date):
    team = get_team(conn, order["team_name"])
    if order["created_by_role"] == SYSTEM_ROLE:
        return True, ""
    if int(team["hard_stop"]) == 1:
        return False, team["hard_stop_reason"] or "隊伍已停止交易"
    if team["suspended_until"]:
        if trade_date < parse_date(team["suspended_until"]):
            return False, f"隊伍停權至 {team['suspended_until']}"
    return True, ""


def execute_order(conn, order, trade_date):
    allowed, reason = order_allowed_to_execute(conn, order, trade_date)
    if not allowed:
        reject_order(conn, order["order_id"], reason)
        return

    team_name = order["team_name"]
    symbol = order["symbol"]
    side = order["side"]
    qty = int(order["quantity"])
    sec = get_security(conn, symbol)
    price = get_close_price(conn, trade_date, symbol)
    gross = price * qty
    fee = gross * FEE_RATE
    tax = gross * tax_rate(sec) if side == "SELL" else 0.0
    pos = get_position(conn, team_name, symbol)
    cash = current_cash(conn, team_name)

    if side == "BUY":
        existing_cost = float(pos["total_cost"]) if pos else 0.0
        if existing_cost + gross > MAX_SINGLE_COST:
            reject_order(conn, order["order_id"], "單一標的總成本超過 4,000 萬元")
            return
        if cash < gross + fee:
            reject_order(conn, order["order_id"], "現金不足")
            return

        old_qty = int(pos["quantity"]) if pos else 0
        new_qty = old_qty + qty
        new_total_cost = existing_cost + gross + fee
        new_avg_cost = new_total_cost / new_qty

        conn.execute(
            """
            INSERT OR REPLACE INTO positions
            (team_name, symbol, quantity, avg_cost, total_cost)
            VALUES (?, ?, ?, ?, ?)
            """,
            (team_name, symbol, new_qty, new_avg_cost, new_total_cost)
        )
        realized_pnl = 0.0
        cash_flow = -(gross + fee)

    else:
        if not pos or int(pos["quantity"]) < qty:
            reject_order(conn, order["order_id"], "可賣出部位不足")
            return

        avg_cost = float(pos["avg_cost"])
        cost_out = avg_cost * qty
        realized_pnl = gross - fee - tax - cost_out
        remain_qty = int(pos["quantity"]) - qty
        remain_cost = avg_cost * remain_qty

        if remain_qty == 0:
            conn.execute(
                "DELETE FROM positions WHERE team_name=? AND symbol=?",
                (team_name, symbol)
            )
        else:
            conn.execute(
                """
                UPDATE positions
                SET quantity=?, avg_cost=?, total_cost=?
                WHERE team_name=? AND symbol=?
                """,
                (remain_qty, avg_cost, remain_cost, team_name, symbol)
            )

        cash_flow = gross - fee - tax

    conn.execute(
        """
        INSERT INTO executions (
            order_id, team_name, symbol, side, exec_date, exec_price,
            quantity, gross_amount, fee, tax, cash_flow, realized_pnl
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            order["order_id"], team_name, symbol, side, trade_date.isoformat(),
            price, qty, gross, fee, tax, cash_flow, realized_pnl
        )
    )

    conn.execute(
        "UPDATE orders SET status='EXECUTED' WHERE order_id=?",
        (order["order_id"],)
    )
    conn.commit()


def add_alert(conn, team_name, alert_date, severity, alert_type, message):
    conn.execute(
        """
        INSERT INTO alerts (team_name, alert_date, severity, alert_type, message)
        VALUES (?, ?, ?, ?, ?)
        """,
        (team_name, alert_date.isoformat(), severity, alert_type, message)
    )
    conn.commit()


def pending_action_exists(conn, team_name, action_type, action_date, symbol=""):
    row = conn.execute(
        """
        SELECT 1 FROM forced_actions
        WHERE team_name=? AND action_type=? AND action_date=?
          AND COALESCE(symbol,'')=COALESCE(?, '')
          AND status='PENDING'
        """,
        (team_name, action_type, action_date.isoformat(), symbol)
    ).fetchone()
    return bool(row)


def schedule_forced_action(conn, team_name, action_date, action_type, reason, symbol=""):
    if pending_action_exists(conn, team_name, action_type, action_date, symbol):
        return
    conn.execute(
        """
        INSERT INTO forced_actions (team_name, action_date, action_type, symbol, reason)
        VALUES (?, ?, ?, ?, ?)
        """,
        (team_name, action_date.isoformat(), action_type, symbol, reason)
    )
    conn.commit()


def process_forced_actions(conn, trade_date):
    rows = conn.execute(
        """
        SELECT * FROM forced_actions
        WHERE action_date=? AND status='PENDING'
        ORDER BY action_id
        """,
        (trade_date.isoformat(),)
    ).fetchall()

    for row in rows:
        team_name = row["team_name"]

        if row["action_type"] == "LIQUIDATE_SYMBOL":
            pos = get_position(conn, team_name, row["symbol"])
            if pos and int(pos["quantity"]) > 0:
                conn.execute(
                    """
                    INSERT INTO orders (
                        team_name, symbol, side, quantity, submitted_at,
                        effective_trade_date, status, reason, allocation_reason,
                        ai_used, ai_timing, created_by_role, created_by_name
                    ) VALUES (?, ?, 'SELL', ?, ?, ?, 'PENDING', ?, '', 0, '', ?, 'RISK_ENGINE')
                    """,
                    (
                        team_name, row["symbol"], int(pos["quantity"]),
                        f"{trade_date.isoformat()} 13:29", trade_date.isoformat(),
                        row["reason"], SYSTEM_ROLE
                    )
                )

        elif row["action_type"] == "LIQUIDATE_ALL":
            poss = conn.execute(
                """
                SELECT symbol, quantity FROM positions
                WHERE team_name=? AND quantity>0
                """,
                (team_name,)
            ).fetchall()
            for p in poss:
                conn.execute(
                    """
                    INSERT INTO orders (
                        team_name, symbol, side, quantity, submitted_at,
                        effective_trade_date, status, reason, allocation_reason,
                        ai_used, ai_timing, created_by_role, created_by_name
                    ) VALUES (?, ?, 'SELL', ?, ?, ?, 'PENDING', ?, '', 0, '', ?, 'RISK_ENGINE')
                    """,
                    (
                        team_name, p["symbol"], int(p["quantity"]),
                        f"{trade_date.isoformat()} 13:29", trade_date.isoformat(),
                        row["reason"], SYSTEM_ROLE
                    )
                )

        elif row["action_type"] == "SUSPEND_UNTIL":
            conn.execute(
                """
                UPDATE teams
                SET suspended_until=?, hard_stop=0, hard_stop_reason=?
                WHERE team_name=?
                """,
                (row["symbol"], row["reason"], team_name)
            )

        elif row["action_type"] == "HARD_STOP":
            conn.execute(
                """
                UPDATE teams
                SET suspended_until=NULL, hard_stop=1, hard_stop_reason=?
                WHERE team_name=?
                """,
                (row["reason"], team_name)
            )

        conn.execute(
            "UPDATE forced_actions SET status='DONE' WHERE action_id=?",
            (row["action_id"],)
        )

    conn.commit()


def execute_pending_orders(conn, trade_date):
    rows = conn.execute(
        """
        SELECT * FROM orders
        WHERE effective_trade_date=? AND status='PENDING'
        ORDER BY order_id
        """,
        (trade_date.isoformat(),)
    ).fetchall()

    for order in rows:
        execute_order(conn, order, trade_date)


def run_risk_checks(conn, trade_date):
    try:
        nxt = get_next_trade_date(conn, trade_date)
    except Exception:
        nxt = None

    for team_name in TEAM_TYPES:
        update_daily_nav(conn, team_name, trade_date)

        nav = conn.execute(
            """
            SELECT * FROM daily_nav
            WHERE team_name=? AND nav_date=?
            """,
            (team_name, trade_date.isoformat())
        ).fetchone()

        if 0 < float(nav["holdings_cost"]) < MIN_TOTAL_HOLDING_COST:
            add_alert(
                conn, team_name, trade_date, "WARN",
                "MIN_TOTAL_HOLDING_COST", "持股總成本低於 2,000 萬元"
            )

        positions = conn.execute(
            """
            SELECT * FROM positions
            WHERE team_name=? AND quantity>0
            """,
            (team_name,)
        ).fetchall()

        if nxt:
            for pos in positions:
                close_price = get_close_price(conn, trade_date, pos["symbol"])
                avg_cost = float(pos["avg_cost"])
                loss_ratio = (avg_cost - close_price) / avg_cost if avg_cost > 0 else 0.0

                if loss_ratio >= MAX_SINGLE_LOSS_RATIO:
                    msg = f"{pos['symbol']} 未實現損失達成本 30%，下一交易日強制出清"
                    add_alert(conn, team_name, trade_date, "HIGH", "SINGLE_STOP", msg)
                    schedule_forced_action(conn, team_name, nxt, "LIQUIDATE_SYMBOL", msg, pos["symbol"])

            project_loss = max(0.0, -float(nav["project_pnl"]))
            june_loss = max(0.0, -float(nav["june_pnl"]))
            july_loss = max(0.0, -float(nav["july_pnl"]))

            if project_loss > MAX_PROJECT_LOSS:
                msg = "專案期間累計虧損超過 2,000 萬元，下一交易日全數出清並停止交易"
                add_alert(conn, team_name, trade_date, "HIGH", "PROJECT_STOP", msg)
                schedule_forced_action(conn, team_name, nxt, "LIQUIDATE_ALL", msg)
                schedule_forced_action(conn, team_name, nxt, "HARD_STOP", msg)

            if trade_date < JUNE_RESET_DATE and june_loss > MAX_MONTH_LOSS:
                msg = "6 月累計虧損超過 1,000 萬元，下一交易日全數出清並停止交易至 7/1"
                add_alert(conn, team_name, trade_date, "HIGH", "JUNE_STOP", msg)
                schedule_forced_action(conn, team_name, nxt, "LIQUIDATE_ALL", msg)
                schedule_forced_action(conn, team_name, nxt, "SUSPEND_UNTIL", msg, JUNE_RESET_DATE.isoformat())

            if trade_date >= JUNE_RESET_DATE and july_loss > MAX_MONTH_LOSS:
                msg = "7 月累計虧損超過 1,000 萬元，下一交易日全數出清並停止交易至專案結束"
                add_alert(conn, team_name, trade_date, "HIGH", "JULY_STOP", msg)
                schedule_forced_action(conn, team_name, nxt, "LIQUIDATE_ALL", msg)
                schedule_forced_action(conn, team_name, nxt, "HARD_STOP", msg)


def run_eod(conn, trade_date_str):
    trade_date = parse_date(trade_date_str)
    process_forced_actions(conn, trade_date)
    execute_pending_orders(conn, trade_date)
    run_risk_checks(conn, trade_date)
    for team_name in TEAM_TYPES:
        update_daily_nav(conn, team_name, trade_date)


def table_to_csv_bytes(rows):
    if not rows:
        return "".encode("utf-8-sig")
    headers = list(rows[0].keys())
    from io import StringIO
    s = StringIO()
    writer = csv.writer(s)
    writer.writerow(headers)
    for r in rows:
        writer.writerow([r[h] for h in headers])
    return s.getvalue().encode("utf-8-sig")


def dataframe_like(rows):
    return [dict(r) for r in rows]


def page_orders(conn):
    st.subheader("建立委託單")

    with st.form("order_form"):
        team = st.selectbox("隊伍", list(TEAM_TYPES.keys()))
        symbol = st.text_input("標的代碼", "2330")
        side = st.selectbox("買賣方向", ["BUY", "SELL"])
        quantity = st.number_input("數量", min_value=1, value=10000, step=1)
        submitted_date = st.date_input("送單日期", dt.date(2026, 6, 22))
        submitted_time = st.text_input("送單時間(HH:MM)", "11:00")
        reason = st.text_input("交易理由")
        allocation_reason = st.text_input("配置理由")
        ai_used = st.checkbox("有使用 AI")
        ai_timing = st.text_input("AI 使用時機", "盤前")
        created_by_role = st.text_input("建立者角色", "前台交易員")
        created_by_name = st.text_input("建立者姓名", "")
        submitted = st.form_submit_button("送出委託")

        if submitted:
            try:
                submitted_at = f"{submitted_date.isoformat()} {submitted_time}"
                place_order(
                    conn, team, symbol, side, int(quantity), submitted_at,
                    reason, allocation_reason, ai_used, ai_timing,
                    created_by_role, created_by_name
                )
                st.success("委託單已建立")
            except Exception as e:
                st.error(str(e))

    st.subheader("最近委託單")
    rows = conn.execute(
        """
        SELECT * FROM orders
        ORDER BY order_id DESC
        LIMIT 30
        """
    ).fetchall()
    if rows:
        st.dataframe(dataframe_like(rows), use_container_width=True)


def save_uploaded_file(uploaded_file, target_name):
    Path("uploads").mkdir(exist_ok=True)
    p = Path("uploads") / target_name
    p.write_bytes(uploaded_file.getvalue())
    return str(p)


def page_import(conn):
    st.subheader("匯入資料")

    tab1, tab2, tab3 = st.tabs(["標的主檔", "價格檔", "委託單"])

    with tab1:
        f = st.file_uploader("上傳 securities CSV", type=["csv"], key="sec")
        if f and st.button("匯入標的主檔"):
            try:
                path = save_uploaded_file(f, "securities_upload.csv")
                import_securities_csv(conn, path)
                st.success("標的主檔匯入完成")
            except Exception as e:
                st.error(str(e))

    with tab2:
        f = st.file_uploader("上傳 prices CSV", type=["csv"], key="price")
        if f and st.button("匯入價格檔"):
            try:
                path = save_uploaded_file(f, "prices_upload.csv")
                import_prices_csv(conn, path)
                st.success("價格檔匯入完成")
            except Exception as e:
                st.error(str(e))

    with tab3:
        f = st.file_uploader("上傳 orders CSV", type=["csv"], key="order")
        if f and st.button("匯入委託單"):
            try:
                path = save_uploaded_file(f, "orders_upload.csv")
                import_orders_csv(conn, path)
                st.success("委託單匯入完成")
            except Exception as e:
                st.error(str(e))


def page_journal(conn):
    st.subheader("交易日誌")

    with st.form("journal_form"):
        team = st.selectbox("隊伍", list(TEAM_TYPES.keys()), key="journal_team")
        journal_date = st.date_input("日期", dt.date(2026, 6, 22), key="journal_date")
        entry_type = st.selectbox("日誌類型", ["TRADE", "DAILY"])
        content = st.text_area("內容")
        ai_used = st.checkbox("有使用 AI", key="journal_ai")
        ai_timing = st.text_input("AI 使用時機", "盤前", key="journal_ai_timing")
        created_by_role = st.text_input("建立者角色", "前台交易員", key="journal_role")
        created_by_name = st.text_input("建立者姓名", "", key="journal_name")
        submitted = st.form_submit_button("新增日誌")

        if submitted:
            try:
                add_journal(
                    conn, team, journal_date.isoformat(), entry_type, content,
                    ai_used, ai_timing, created_by_role, created_by_name
                )
                st.success("交易日誌已新增")
            except Exception as e:
                st.error(str(e))

    rows = conn.execute(
        """
        SELECT * FROM journals
        ORDER BY journal_id DESC
        LIMIT 30
        """
    ).fetchall()
    if rows:
        st.dataframe(dataframe_like(rows), use_container_width=True)


def page_eod(conn):
    st.subheader("每日收盤後處理 EOD")
    run_date = st.date_input("交易日", dt.date(2026, 6, 22))
    if st.button("執行 EOD"):
        try:
            run_eod(conn, run_date.isoformat())
            st.success("EOD 完成")
        except Exception as e:
            st.error(str(e))


def page_reports(conn):
    st.subheader("報表")
    team = st.selectbox("隊伍", list(TEAM_TYPES.keys()), key="report_team")
    report_date = st.date_input("報表日期", dt.date(2026, 6, 22), key="report_date")
    report_date_str = report_date.isoformat()

    try:
        update_daily_nav(conn, team, report_date)
    except Exception as e:
        st.warning(f"尚無法更新績效：{e}")

    nav_row = conn.execute(
        """
        SELECT * FROM daily_nav
        WHERE team_name=? AND nav_date=?
        """,
        (team, report_date_str)
    ).fetchone()

    st.markdown("### 每日交易績效總表")
    if nav_row:
        st.dataframe([dict(nav_row)], use_container_width=True)

    trade_rows = conn.execute(
        """
        SELECT
            o.order_id, o.team_name, o.symbol, o.side, o.quantity,
            o.submitted_at, o.effective_trade_date, o.reason,
            o.allocation_reason, o.ai_used, o.ai_timing,
            o.created_by_role, o.created_by_name,
            o.status, o.rejection_reason,
            e.exec_price, e.gross_amount, e.fee, e.tax, e.cash_flow, e.realized_pnl
        FROM orders o
        LEFT JOIN executions e ON o.order_id = e.order_id
        WHERE o.team_name=? AND o.effective_trade_date=?
        ORDER BY o.order_id
        """,
        (team, report_date_str)
    ).fetchall()

    st.markdown("### 每日買賣日報表")
    if trade_rows:
        st.dataframe(dataframe_like(trade_rows), use_container_width=True)
        st.download_button(
            "下載每日買賣日報表 CSV",
            data=table_to_csv_bytes(trade_rows),
            file_name=f"{team}_{report_date_str}_trade_report.csv",
            mime="text/csv",
        )

    pos_rows = conn.execute(
        """
        SELECT * FROM positions
        WHERE team_name=? AND quantity>0
        ORDER BY symbol
        """,
        (team,)
    ).fetchall()

    st.markdown("### 持股部位")
    if pos_rows:
        enriched = []
        for r in pos_rows:
            try:
                close_price = get_close_price(conn, report_date, r["symbol"])
            except Exception:
                close_price = None
            market_val = close_price * int(r["quantity"]) if close_price is not None else None
            unrealized = market_val - float(r["total_cost"]) if market_val is not None else None
            enriched.append({
                "team_name": team,
                "symbol": r["symbol"],
                "quantity": r["quantity"],
                "avg_cost": r["avg_cost"],
                "total_cost": r["total_cost"],
                "close_price": close_price,
                "market_value": market_val,
                "unrealized_pnl": unrealized,
            })
        st.dataframe(enriched, use_container_width=True)


def page_status(conn):
    st.subheader("系統狀態")

    team_rows = []
    for team_name in TEAM_TYPES:
        t = get_team(conn, team_name)
        team_rows.append({
            "team_name": team_name,
            "cash": current_cash(conn, team_name),
            "suspended_until": t["suspended_until"],
            "hard_stop": t["hard_stop"],
            "hard_stop_reason": t["hard_stop_reason"],
        })
    st.markdown("### 隊伍狀態")
    st.dataframe(team_rows, use_container_width=True)

    st.markdown("### 最近警示")
    alerts = conn.execute(
        """
        SELECT * FROM alerts
        ORDER BY alert_id DESC
        LIMIT 50
        """
    ).fetchall()
    if alerts:
        st.dataframe(dataframe_like(alerts), use_container_width=True)

    st.markdown("### 最近強制動作")
    actions = conn.execute(
        """
        SELECT * FROM forced_actions
        ORDER BY action_id DESC
        LIMIT 50
        """
    ).fetchall()
    if actions:
        st.dataframe(dataframe_like(actions), use_container_width=True)


def main():
    st.set_page_config(page_title="Paper Trade 平台", layout="wide")
    st.title("Paper Trade 平台")
    st.caption("股票投資組 / ETF投資組 模擬交易管理")

    conn = connect()
    init_db(conn)

    page = st.sidebar.radio(
        "功能選單",
        ["下單", "匯入資料", "交易日誌", "跑 EOD", "報表", "系統狀態"]
    )

    if page == "下單":
        page_orders(conn)
    elif page == "匯入資料":
        page_import(conn)
    elif page == "交易日誌":
        page_journal(conn)
    elif page == "跑 EOD":
        page_eod(conn)
    elif page == "報表":
        page_reports(conn)
    elif page == "系統狀態":
        page_status(conn)


if __name__ == "__main__":
    main()
