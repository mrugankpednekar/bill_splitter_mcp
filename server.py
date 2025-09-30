# server.py
from fastmcp import FastMCP
import sqlite3, time, hashlib, uuid
from typing import List, Dict

mcp = FastMCP("SplitFast")

DB = "splitfast.db"

def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()

def _init_db():
    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS groups
      (id TEXT PRIMARY KEY, name TEXT, members TEXT, secret_hash TEXT, created_at INTEGER)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS expenses
      (id TEXT PRIMARY KEY, group_id TEXT, payer TEXT, amount_cents INTEGER,
       participants TEXT, note TEXT, ts INTEGER)""")
    con.commit()
    con.close()

def _auth(group_id: str, secret: str):
    con = sqlite3.connect(DB); cur = con.cursor()
    cur.execute("SELECT secret_hash FROM groups WHERE id=?", (group_id,))
    row = cur.fetchone(); con.close()
    if not row or row[0] != _h(secret):
        raise PermissionError("Invalid group or secret")

@mcp.tool()
def create_group(name: str, members: List[str]) -> Dict:
    """Create a group and return {group_id, secret} to share privately."""
    gid = str(uuid.uuid4()); secret = str(uuid.uuid4())
    con = sqlite3.connect(DB); cur = con.cursor()
    cur.execute("INSERT INTO groups VALUES(?,?,?,?,?)",
                (gid, name, ",".join(members), _h(secret), int(time.time())))
    con.commit(); con.close()
    return {"group_id": gid, "secret": secret}

@mcp.tool()
def add_expense(group_id: str, secret: str, payer: str, amount: float,
                participants: List[str], note: str = "") -> Dict:
    """Record an expense split among participants."""
    _auth(group_id, secret)
    eid = str(uuid.uuid4())
    con = sqlite3.connect(DB); cur = con.cursor()
    cur.execute("INSERT INTO expenses VALUES(?,?,?,?,?,?,?)",
                (eid, group_id, payer, int(round(amount*100)), ",".join(participants), note, int(time.time())))
    con.commit(); con.close()
    return {"expense_id": eid}

@mcp.tool()
def balances(group_id: str, secret: str) -> List[Dict]:
    """Return min-cash-flow settlement transfers."""
    _auth(group_id, secret)
    con = sqlite3.connect(DB); cur = con.cursor()
    cur.execute("SELECT payer, amount_cents, participants FROM expenses WHERE group_id=?", (group_id,))
    net = {}
    for payer, cents, parts in cur.fetchall():
        parts_list = [p for p in parts.split(",") if p]
        share = cents / max(1, len(parts_list))
        net[payer] = net.get(payer, 0) + cents
        for p in parts_list:
            net[p] = net.get(p, 0) - share
    con.close()
    creditors = sorted([(u,a) for u,a in net.items() if a>0], key=lambda x:-x[1])
    debtors   = sorted([(u,-a) for u,a in net.items() if a<0], key=lambda x:-x[1])
    i=j=0; res=[]
    while i<len(debtors) and j<len(creditors):
        duser, damt = debtors[i]; cuser, camt = creditors[j]
        amt = int(min(damt, camt))
        if amt>0: res.append({"from": duser, "to": cuser, "amount": amt/100})
        debtors[i]=(duser, damt-amt); creditors[j]=(cuser, camt-amt)
        if debtors[i][1]==0: i+=1
        if creditors[j][1]==0: j+=1
    return res

if __name__ == "__main__":
    _init_db()
    # Default FastMCP run() starts STDIO (good for local testing).
    mcp.run()

