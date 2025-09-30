# server.py — Splitwise-simple MCP
from fastmcp import FastMCP
import sqlite3, time, hashlib, uuid, re
from typing import List, Dict

mcp = FastMCP("SplitFast")
DB = "splitfast.db"

# --------------------- DB + utils ---------------------
def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()

def _now() -> int:
    return int(time.time())

def _init_db():
    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS groups
      (id TEXT PRIMARY KEY, name TEXT, members TEXT, secret_hash TEXT, created_at INTEGER)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS expenses
      (id TEXT PRIMARY KEY, group_id TEXT, payer TEXT, amount_cents INTEGER,
       participants TEXT, note TEXT, ts INTEGER)""")
    con.commit(); con.close()

def _auth(group_id: str, secret: str):
    con = sqlite3.connect(DB); cur = con.cursor()
    cur.execute("SELECT secret_hash FROM groups WHERE id=?", (group_id,))
    row = cur.fetchone(); con.close()
    if not row or row[0] != _h(secret):
        raise PermissionError("Invalid group or secret")

def _insert_expense(group_id: str, payer: str, amount: float, participants_csv: str, note: str):
    eid = str(uuid.uuid4())
    con = sqlite3.connect(DB); cur = con.cursor()
    cur.execute("INSERT INTO expenses VALUES(?,?,?,?,?,?,?)",
                (eid, group_id, payer, int(round(amount*100)), participants_csv, note, _now()))
    con.commit(); con.close()
    return {"expense_id": eid}

# --------------------- Core tools ---------------------
@mcp.tool()
def create_group(name: str, members: List[str]) -> Dict:
    """Create a group and return {group_id, secret}."""
    gid = str(uuid.uuid4()); secret = str(uuid.uuid4())
    con = sqlite3.connect(DB); cur = con.cursor()
    cur.execute("INSERT INTO groups VALUES(?,?,?,?,?)",
                (gid, name, ",".join(members), _h(secret), _now()))
    con.commit(); con.close()
    return {"group_id": gid, "secret": secret}

@mcp.tool()
def record_debt(group_id: str, secret: str, debtor: str, creditor: str, amount: float, note: str = "") -> Dict:
    """
    Splitwise-style direct debt: 'debtor owes creditor amount'.
    Implementation: store one expense paid by creditor, with participants=[debtor] for full amount.
    """
    _auth(group_id, secret)
    return _insert_expense(group_id, payer=creditor, amount=amount, participants_csv=debtor,
                           note=note or f"{debtor} owes {creditor} {amount}")

@mcp.tool()
def add_split(group_id: str, secret: str, payer: str, amount: float,
              participants: List[str], include_payer: bool = True, note: str = "") -> Dict:
    """
    Equal split receipt. If include_payer=True, payer also owes a share (Splitwise default).
    Example: payer='Owen', amount=60, participants=['Owen','Mrugank','David'] -> shares=$20 each.
    """
    _auth(group_id, secret)
    parts = participants[:]
    if not include_payer and payer in parts:
        parts = [p for p in parts if p != payer]
    if len(parts) == 0:
        raise ValueError("participants cannot be empty")
    # Implementation detail:
    # Store a single expense 'amount' paid by payer and split across comma-joined parts.
    return _insert_expense(group_id, payer=payer, amount=amount, participants_csv=",".join(parts),
                           note=note or f"split by {', '.join(parts)}")

@mcp.tool()
def history(group_id: str, secret: str, limit: int = 20) -> List[Dict]:
    """Return recent raw entries for the group."""
    _auth(group_id, secret)
    con = sqlite3.connect(DB); cur = con.cursor()
    cur.execute("""SELECT payer, amount_cents, participants, note, ts
                   FROM expenses WHERE group_id=?
                   ORDER BY ts DESC LIMIT ?""", (group_id, limit))
    rows = cur.fetchall(); con.close()
    res = []
    for payer, cents, parts, note, ts in rows:
        res.append({"payer": payer, "amount": cents/100, "participants": parts.split(",") if parts else [], "note": note, "ts": ts})
    return res

@mcp.tool()
def balances(group_id: str, secret: str) -> List[Dict]:
    """
    Return minimal settlement transfers: [{from, to, amount}].
    """
    _auth(group_id, secret)
    con = sqlite3.connect(DB); cur = con.cursor()
    cur.execute("SELECT payer, amount_cents, participants FROM expenses WHERE group_id=?", (group_id,))
    net = {}
    for payer, cents, parts in cur.fetchall():
        parts_list = [p for p in parts.split(",") if p]
        if not parts_list:
            # Defensive: if somehow empty, treat as nobody owing (edge)
            continue
        share = cents / len(parts_list)
        net[payer] = net.get(payer, 0) + cents
        for p in parts_list:
            net[p] = net.get(p, 0) - share
    con.close()
    # build minimal cash flow
    creditors = sorted([(u,a) for u,a in net.items() if a >  1e-9], key=lambda x:-x[1])
    debtors   = sorted([(u,-a) for u,a in net.items() if a < -1e-9], key=lambda x:-x[1])
    i=j=0; res=[]
    while i < len(debtors) and j < len(creditors):
        duser, damt = debtors[i]; cuser, camt = creditors[j]
        pay = int(min(damt, camt))
        if pay > 0:
            res.append({"from": duser, "to": cuser, "amount": pay/100})
        debtors[i]   = (duser, damt - pay)
        creditors[j] = (cuser, camt - pay)
        if debtors[i][1] == 0: i += 1
        if creditors[j][1] == 0: j += 1
    return res

# -------- Free-form quick settle (paste a sentence; no DB writes) --------
def _norm_whitespace(s: str) -> str:
    return re.sub(r'[\s,]+', ' ', s.strip())

def _cap_name(name: str) -> str:
    return ' '.join([t.capitalize() for t in name.split()])

def _parse_debts(text: str) -> list[tuple[str, str, float]]:
    cleaned = _norm_whitespace(text.lower())
    pattern = re.compile(r'([a-z]+)\s+owes\s+([a-z]+)\s+\$?(\d+(?:\.\d{1,2})?)', re.IGNORECASE)
    edges=[]
    for m in pattern.finditer(cleaned):
        debtor=_cap_name(m.group(1)); creditor=_cap_name(m.group(2)); amount=float(m.group(3))
        edges.append((debtor, creditor, amount))
    return edges

def _settle_edges(edges: list[tuple[str,str,float]]) -> list[dict]:
    net={}
    for debtor, creditor, amt in edges:
        net[debtor]=net.get(debtor,0)-amt
        net[creditor]=net.get(creditor,0)+amt
    creditors=sorted([(u,a) for u,a in net.items() if a>1e-9], key=lambda x:-x[1])
    debtors=sorted([(u,-a) for u,a in net.items() if a<-1e-9], key=lambda x:-x[1])
    i=j=0; res=[]
    while i<len(debtors) and j<len(creditors):
        duser, damt=debtors[i]; cuser, camt=creditors[j]
        pay=min(damt,camt)
        res.append({"from": duser, "to": cuser, "amount": round(pay,2)})
        debtors[i]=(duser, round(damt-pay,2))
        creditors[j]=(cuser, round(camt-pay,2))
        if debtors[i][1]<=1e-9: i+=1
        if creditors[j][1]<=1e-9: j+=1
    return res

@mcp.tool()
def quick_settle(text: str) -> List[Dict]:
    """
    Paste: 'owen owes mrugank 20, mrugank owes david 15, barath owes david 7, owen owes barath 14'
    Returns minimal settlement without writing to DB.
    """
    edges=_parse_debts(text)
    if not edges: return []
    return _settle_edges(edges)

# --------------------- main ---------------------
if __name__ == "__main__":
    _init_db()
    # Streamable HTTP for hosting (Smithery)
    mcp.run(transport="http", host="0.0.0.0", port=8080)

