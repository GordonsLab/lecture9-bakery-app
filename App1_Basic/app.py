"""Local bakery tracker. Python standard library only."""
import argparse
import csv
import datetime as dt
import html
import json
from pathlib import Path
import re
import sqlite3
from http.server import BaseHTTPRequestHandler, HTTPServer
import webbrowser

STRUCTURED = False
DEFAULT_PORT = 8000
ROOT = Path(__file__).resolve().parent
START = dt.date(2026, 11, 23)

def product(text):
    m = re.fullmatch(r'(\d+)\s+(dozen cupcakes|cakes?)', text.strip(), re.I)
    if not m or int(m[1]) <= 0:
        raise ValueError('Unrecognized product: ' + text)
    return ('cupcakes' if m[2].lower().startswith('dozen') else 'cake', int(m[1]))

def hours(p, q): return q * (0.5 if p == 'cupcakes' else 2)
def price(p, q): return q * (40 if p == 'cupcakes' else 120)
def date(text):
    return dt.datetime.strptime(text.strip() + ' 2026', '%a %b %d %Y').date().isoformat()

class Bakery:
    def __init__(self, db=None):
        self.db = sqlite3.connect(str(db or ROOT / 'bakery.db'))
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS days(day TEXT PRIMARY KEY,helper INTEGER NOT NULL DEFAULT 0 CHECK(helper IN (0,1)));
        CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY,customer TEXT,product TEXT,quantity INTEGER,pickup TEXT REFERENCES days(day),status TEXT,received TEXT);
        CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY,day TEXT,customer TEXT,body TEXT,outcome TEXT);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,day TEXT,detail TEXT);
        CREATE TABLE IF NOT EXISTS undo_state(id INTEGER PRIMARY KEY CHECK(id=1),snapshot TEXT NOT NULL,label TEXT NOT NULL);
        ''')
        self.db.execute('PRAGMA foreign_keys=ON')
        if not self.db.execute('SELECT 1 FROM meta').fetchone():
            with self.db:
                self.db.execute('INSERT INTO meta VALUES (?,?)', ('today', START.isoformat()))
                for i in range(15): self.db.execute('INSERT INTO days(day) VALUES (?)', ((START + dt.timedelta(days=i)).isoformat(),))
                with (ROOT / 'existing_orders.csv').open(newline='', encoding='utf-8-sig') as f:
                    for r in csv.DictReader(f):
                        p, q = product(r['item'])
                        day = dt.datetime.strptime(r['pickup_day'] + ' 2026', '%a %d %b %Y').date().isoformat()
                        self.db.execute('INSERT INTO orders VALUES (?,?,?,?,?,?,?)', (int(r['order'][1:]),r['customer'],p,q,day,'Accepted','2026-11-20'))
    @property
    def today(self): return self.db.execute("SELECT value FROM meta WHERE key='today'").fetchone()[0]
    def log(self, text): self.db.execute('INSERT INTO audit(day,detail) VALUES (?,?)',(self.today,text))
    def booked(self, day, exclude=None):
        return sum(hours(r['product'],r['quantity']) for r in self.db.execute("SELECT * FROM orders WHERE pickup=? AND status IN ('Accepted','Picked up')",(day,)) if r['id'] != exclude)
    def capacity(self, day):
        r = self.db.execute('SELECT helper FROM days WHERE day=?',(day,)).fetchone()
        return 8 + 4 * (r[0] if r else 0)
    def reason(self, pickup, p, q, received=None, exclude=None):
        if (dt.date.fromisoformat(pickup)-dt.date.fromisoformat(received or self.today)).days < 2: return 'Needs at least 2 days notice'
        if hours(p,q) + self.booked(pickup,exclude) > self.capacity(pickup): return 'Not enough oven hours; consider a helper'
        return ''
    def refresh(self):
        for r in list(self.db.execute("SELECT * FROM orders WHERE status IN ('Requested','Incompatible')")):
            why = self.reason(r['pickup'],r['product'],r['quantity'],r['received'])
            # An unaccepted request cannot be accepted once its booking deadline passes.
            if not why: why = self.reason(r['pickup'],r['product'],r['quantity'])
            self.db.execute('UPDATE orders SET status=? WHERE id=?',('Incompatible' if why else 'Requested',r['id']))
    def fetch(self):
        d = dt.date.fromisoformat(self.today)
        if not 0 <= (d-START).days <= 6: return 'No supplied messages for this day.'
        file = ROOT / ('InstagramOrders_' + d.strftime('%A') + '.html')
        bodies = re.findall(r'<p class="message">(.*?)</p>',file.read_text(encoding='utf-8'),re.S)
        n=0
        for i, raw in enumerate(bodies):
            key=f'{self.today}:{i}'
            if self.db.execute('SELECT 1 FROM messages WHERE id=?',(key,)).fetchone(): continue
            body=html.unescape(raw)
            fields=dict(line.split(':',1) for line in body.splitlines() if ':' in line)
            who=fields['Name'].strip(); text=fields['Order'].strip(); pickup=date(fields['Pickup Date'])
            self.db.execute('INSERT OR IGNORE INTO days(day) VALUES (?)',(pickup,))
            old=self.db.execute("SELECT * FROM orders WHERE lower(customer)=lower(?) AND pickup=? AND status IN ('Accepted','Requested','Incompatible') ORDER BY id DESC",(who,pickup)).fetchone()
            if text.lower()=='cancel' or text.lower().startswith('change to '):
                why='No active matching order' if old is None else ''
                if not why and (dt.date.fromisoformat(pickup)-d).days < 2: why='Needs at least 2 days notice'
                if text.lower()=='cancel':
                    if not why: self.db.execute("UPDATE orders SET status='Cancelled' WHERE id=?",(old['id'],))
                    outcome='Cancellation blocked: '+why if why else 'Cancelled order #'+str(old['id'])
                else:
                    p,q=product(text[10:])
                    if not why: why=self.reason(pickup,p,q,exclude=old['id'])
                    if not why: self.db.execute('UPDATE orders SET product=?,quantity=? WHERE id=?',(p,q,old['id']))
                    outcome='Change blocked: '+why if why else 'Changed order #'+str(old['id'])
            else:
                p,q=product(text); why=self.reason(pickup,p,q)
                self.db.execute('INSERT INTO orders(customer,product,quantity,pickup,status,received) VALUES (?,?,?,?,?,?)',(who,p,q,pickup,'Incompatible' if why else 'Requested',self.today))
                outcome='New request: '+(why or 'Ready for baker decision')
            self.db.execute('INSERT INTO messages VALUES (?,?,?,?,?)',(key,self.today,who,body,outcome)); self.log(who+': '+outcome); n+=1
        self.refresh()
        return f'Fetched {n} new messages. Changes and cancellations checked automatically.'
    def accept(self, ident):
        r=self.db.execute('SELECT * FROM orders WHERE id=?',(ident,)).fetchone()
        if not r or r['status'] not in ('Requested','Incompatible'): raise ValueError('This order is no longer pending.')
        why=self.reason(r['pickup'],r['product'],r['quantity'],r['received']) or self.reason(r['pickup'],r['product'],r['quantity'])
        if why: raise ValueError(why)
        self.db.execute("UPDATE orders SET status='Accepted' WHERE id=?",(ident,)); self.log(f'Accepted order #{ident}'); self.refresh()
    def action(self, action, data):
        tables=('meta','days','orders','messages','audit')
        with self.db:
            if action=='undo':
                saved=self.db.execute('SELECT snapshot,label FROM undo_state WHERE id=1').fetchone()
                if not saved: raise ValueError('Nothing to undo. Make a change first.')
                snapshot=json.loads(saved['snapshot'])
                for table in ('orders','messages','audit','meta','days'): self.db.execute('DELETE FROM '+table)
                for table in tables:
                    for row in snapshot[table]:
                        columns=','.join(row)
                        self.db.execute(f'INSERT INTO {table} ({columns}) VALUES ({",".join("?" for _ in row)})',tuple(row.values()))
                self.db.execute('DELETE FROM undo_state')
                return 'Undid '+saved['label']+'. The previous orders, messages and date are restored.'
            labels={'fetch':'message fetch','accept':'order acceptance','reject':'order rejection','helper':'helper booking','next':'day close'}
            if action not in labels: raise ValueError('Unknown action. Refresh the page and try again.')
            before={table:[dict(row) for row in self.db.execute('SELECT * FROM '+table)] for table in tables}
            self.db.execute('INSERT OR REPLACE INTO undo_state VALUES (1,?,?)',(json.dumps(before),labels[action]))
            result=self._action(action,data)
            after={table:[dict(row) for row in self.db.execute('SELECT * FROM '+table)] for table in tables}
            if before==after:
                # A repeated fetch must not replace an earlier meaningful undo point.
                self.db.rollback()
            return result
    def _action(self, action, data):
        if action=='fetch': return self.fetch()
        if action=='accept': self.accept(int(data['id'])); return 'Order accepted.'
        if action=='reject':
            c=self.db.execute("UPDATE orders SET status='Rejected' WHERE id=? AND status IN ('Requested','Incompatible')",(int(data['id']),))
            if not c.rowcount: raise ValueError('This order is no longer pending.')
            self.log('Rejected order #'+str(data['id'])); self.refresh(); return 'Order rejected.'
        if action=='helper':
            day=data['day']
            if (dt.date.fromisoformat(day)-dt.date.fromisoformat(self.today)).days < 2: raise ValueError('Helpers need 2 days notice.')
            c=self.db.execute('UPDATE days SET helper=1 WHERE day=? AND helper=0',(day,))
            if not c.rowcount: raise ValueError('Helper already booked or day unavailable.')
            self.log('Booked $100 helper for '+day); self.refresh(); return 'Helper booked: 12 oven hours available.'
        if action=='next':
            self.fetch() # Never skip unseen messages when advancing.
            accepted=0
            for r in list(self.db.execute("SELECT id FROM orders WHERE status IN ('Requested','Incompatible') ORDER BY id")):
                try: self.accept(r['id']); accepted+=1
                except ValueError: pass
            self.db.execute("UPDATE orders SET status='Picked up' WHERE status='Accepted' AND pickup<=?",(self.today,))
            nxt=(dt.date.fromisoformat(self.today)+dt.timedelta(days=1)).isoformat()
            self.log(f'Closed day at 5 PM; accepted {accepted} requests')
            self.db.execute("UPDATE meta SET value=? WHERE key='today'",(nxt,)); self.refresh()
            return f'Accepted {accepted} eligible requests, completed pickups at 5 PM and advanced one day.'
        raise ValueError('Unknown action.')
    def state(self):
        orders=[]
        for r in self.db.execute('SELECT * FROM orders ORDER BY pickup,id'):
            r=dict(r); r['hours']=hours(r['product'],r['quantity']); r['sales']=price(r['product'],r['quantity'])
            r['reason']=(self.reason(r['pickup'],r['product'],r['quantity'],r['received']) or self.reason(r['pickup'],r['product'],r['quantity'])) if r['status'] in ('Requested','Incompatible') else ''
            orders.append(r)
        days=[dict(r,booked=self.booked(r['day']),capacity=self.capacity(r['day'])) for r in self.db.execute('SELECT * FROM days ORDER BY day')]
        committed=sum(r['sales'] for r in orders if r['status'] in ('Accepted','Picked up'))
        realized=sum(r['sales'] for r in orders if r['status']=='Picked up')
        costs=sum(r['helper']*100 for r in days)
        undo=self.db.execute('SELECT label FROM undo_state WHERE id=1').fetchone()
        return dict(today=self.today,orders=orders,days=days,undoLabel=undo['label'] if undo else None,messages=[dict(r) for r in self.db.execute('SELECT * FROM messages ORDER BY id DESC')],audit=[dict(r) for r in self.db.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 100')],committed=committed,realized=realized,costs=costs,net=committed-costs,structured=STRUCTURED)

PAGE = r'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Butter & Batch | Bakery tracker</title>
<style>
:root{font-family:Segoe UI,Arial,sans-serif;color:#253b3b;background:#f4f7f6;font-size:16px}*{box-sizing:border-box}body{margin:0}header{background:#173f3d;color:white;padding:28px max(24px,calc((100vw - 1400px)/2));display:flex;justify-content:space-between;gap:20px;align-items:center}h1{font-family:Georgia,serif;font-size:34px;margin:5px 0}h2{font-size:22px}h3{font-size:17px;margin:8px 0}p{line-height:1.5}small{font-size:14px}main{max-width:1450px;margin:auto;padding:24px}.muted{color:#526968}.eyebrow{letter-spacing:.15em;font-size:14px}button,.link{font:inherit;padding:10px 14px;border:1px solid #bdcfca;border-radius:8px;background:white;color:#173f3d;cursor:pointer;text-decoration:none;display:inline-block}button:hover{background:#e8f1ec}button:disabled{opacity:.5;cursor:default}.primary{background:#d4f0b8;border-color:#d4f0b8}.toolbar{display:flex;gap:10px;flex-wrap:wrap;align-items:center}.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin:20px 0}.stat,section{background:white;border:1px solid #dbe5e0;border-radius:12px;padding:20px}.stat strong{display:block;font-size:30px;margin-top:8px}.board{display:grid;grid-template-columns:repeat(7,minmax(155px,1fr));gap:12px;overflow:auto;padding-bottom:12px}.day{background:white;border:1px solid #dbe5e0;border-radius:10px;padding:15px;min-width:155px}.today{border:2px solid #236b60}.day p{border-top:1px solid #e5ece9;padding-top:10px;font-size:14px}.day button{font-size:14px;width:100%;padding:8px 4px}progress{width:100%;accent-color:#247566;margin:12px 0}.columns{display:grid;grid-template-columns:1.4fr 1fr;gap:20px;margin-top:20px}.request{padding:14px 0;border-top:1px solid #e0e9e4;display:flex;justify-content:space-between;gap:14px}.badge{display:inline-block;border-radius:5px;background:#edf3ee;padding:4px 7px;font-size:14px}.warning{color:#934324;background:#fff0e7}.actions{display:flex;align-items:center;gap:8px}.reason{color:#934324;font-size:14px}.notice{padding:14px 18px;background:#e0eee4;border-radius:8px;margin:16px 0;min-height:48px}details{margin-top:20px}summary{cursor:pointer;font-weight:600;padding:10px 0}table{border-collapse:collapse;width:100%;font-size:14px}td,th{text-align:left;padding:10px;border-bottom:1px solid #e1e9e4}.scroll{overflow:auto}pre{white-space:pre-wrap;font:inherit;font-size:14px}.basic .board{display:none}.basic .columns{grid-template-columns:1fr 1fr}button:focus-visible,a:focus-visible{outline:3px solid #e19b3e;outline-offset:3px}@media(max-width:900px){.stats{grid-template-columns:1fr 1fr}.columns,.basic .columns{grid-template-columns:1fr}header{align-items:flex-start;flex-direction:column}.board{grid-template-columns:repeat(7,180px)}.request{flex-direction:column}.actions{flex-wrap:wrap}}
</style><header><div><div class="eyebrow">BUTTER & BATCH</div><h1 id="title">Bakery order desk</h1><small>Less juggling. More baking.</small></div><a class="link" href="/README.html">How to use this app</a></header><main><div class="toolbar"><h2 id="today" style="margin-right:auto"></h2><button onclick="act('fetch')">Fetch today's DMs</button><button id="undo-button" onclick="act('undo')" disabled>Undo last action</button><button class="primary" onclick="advance()">Accept all & go to next day</button></div><p class="muted">Lecture simulation · Pickups at 5 PM · 2 days notice required · $40 / dozen cupcakes · $120 / cake</p><div id="notice" class="notice" role="status" aria-live="polite">Step 1: Fetch today's DMs. Step 2: review requests or book a helper. Step 3: accept orders and close the day. Each change saves automatically.</div><div id="stats" class="stats"></div><h2 id="boardtitle">The baking calendar</h2><div id="board" class="board"></div><div class="columns"><section><h2>New requests</h2><p class="muted">Accept individually, or accept eligible requests in arrival order when closing the day.</p><div id="requests"></div></section><section><h2>Needs attention</h2><p class="muted">Capacity conflicts can become eligible after a helper is booked. Late requests stay blocked.</p><div id="blocked"></div></section></div><section style="margin-top:20px"><h2 id="ordertitle">Orders & daily capacity</h2><div id="capacity"></div><label for="filter">Show orders: </label><select id="filter" onchange="drawOrders()"><option>All</option><option>Accepted</option><option>Requested</option><option>Incompatible</option><option>Picked up</option><option>Cancelled</option><option>Rejected</option></select><div class="scroll" id="orders"></div></section><details><summary>Messages, changes & cancellations</summary><section id="messages"></section></details><details><summary>Activity history</summary><section id="audit"></section></details><p class="muted">Money shown across all loaded pickup dates, including next week. Net = committed sales minus helpers; ingredients and other costs are not supplied.</p></main><script>
let state;const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));const money=n=>'$'+n.toLocaleString();const day=d=>new Date(d+'T12:00:00').toLocaleDateString('en-CA',{weekday:'short',month:'short',day:'numeric'});const item=o=>o.quantity+(o.product==='cake'?' cake'+(o.quantity>1?'s':''):' dozen cupcakes');
async function load(){const r=await fetch('/api/state');state=await r.json();render()}
async function act(action,data={}){
const controls=[...document.querySelectorAll('button')].map(b=>[b,b.disabled]);controls.forEach(([b])=>b.disabled=true);document.querySelector('main').setAttribute('aria-busy','true');const notice=document.getElementById('notice');notice.setAttribute('role','status');notice.textContent=action==='next'?'Checking requests and completing pickups…':'Saving your change…';
try{const r=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action,...data})});const j=await r.json();notice.setAttribute('role',r.ok?'status':'alert');notice.classList.toggle('warning',!r.ok);notice.textContent=j.message||j.error;await load()}
catch(e){notice.setAttribute('role','alert');notice.classList.add('warning');notice.textContent='Cannot reach the bakery app. Restart Start App.cmd, then refresh this page. Check the activity history before retrying a change.'}
finally{controls.forEach(([b,disabled])=>{if(b.isConnected)b.disabled=disabled});document.getElementById('undo-button').disabled=!state?.undoLabel;document.querySelector('main').setAttribute('aria-busy','false')}
}
function advance(){if(confirm('Fetch remaining DMs, accept all eligible requests in arrival order, complete today’s 5 PM pickups and move to tomorrow?'))act('next')}
function helper(d){if(confirm('Book a helper for '+day(d)+' for $100? This adds 4 hours.'))act('helper',{day:d})}
function req(o){return `<div class="request"><div><h3>${esc(o.customer)} <small>#${o.id}</small></h3><p>${esc(item(o))} · ${o.hours} h · ${money(o.sales)}<br>${day(o.pickup)}</p>${o.reason?`<p class="reason">${esc(o.reason)}</p>`:''}</div><div class="actions">${!o.reason?`<button class="primary" onclick="act('accept',{id:${o.id}})">Accept</button>`:''}<button onclick="act('reject',{id:${o.id}})">Reject</button></div></div>`}
function drawOrders(){let f=document.getElementById('filter').value;document.getElementById('orders').innerHTML='<table><thead><tr><th>Order / customer</th><th>Product</th><th>Pickup</th><th>Oven hours</th><th>Sales</th><th>Status</th></tr></thead><tbody>'+state.orders.filter(o=>f==='All'||o.status===f).map(o=>`<tr><td>#${o.id} ${esc(o.customer)}</td><td>${esc(item(o))}</td><td>${day(o.pickup)}</td><td>${o.hours}</td><td>${money(o.sales)}</td><td><span class="badge">${o.status}</span></td></tr>`).join('')+'</tbody></table>'}
function render(){let undo=document.getElementById('undo-button');undo.disabled=!state.undoLabel;undo.textContent=state.undoLabel?'Undo '+state.undoLabel:'Undo last action';document.body.className=state.structured?'':'basic';document.getElementById('title').textContent=state.structured?'Bakery planning board':'Bakery order desk';document.getElementById('today').textContent=day(state.today)+', 2026';document.getElementById('boardtitle').hidden=!state.structured;document.getElementById('stats').innerHTML=[['Committed sales',state.committed],['Picked-up sales',state.realized],['Helper bookings',state.costs],['Sales less helpers',state.net]].map(([l,v])=>`<div class="stat"><small>${l}</small><strong>${money(v)}</strong></div>`).join('');let days=state.days.filter(d=>d.day>=state.today).slice(0,7);document.getElementById('board').innerHTML=days.map(d=>`<div class="day ${d.day===state.today?'today':''}"><h3>${day(d.day)}</h3><strong>${d.booked} / ${d.capacity} h booked</strong><progress value="${d.booked}" max="${d.capacity}" aria-label="${day(d.day)} booked hours"></progress><small>${d.capacity-d.booked} h available</small>${state.orders.filter(o=>o.pickup===d.day&&o.status==='Accepted').map(o=>`<p><b>${esc(o.customer)}</b><br>${esc(item(o))}</p>`).join('')}<p><button ${d.helper||((new Date(d.day)-new Date(state.today))/86400000)<2?'disabled':''} onclick="helper('${d.day}')">${d.helper?'Helper booked · $100':'+4 hours · $100 helper'}</button></p></div>`).join('');document.getElementById('capacity').innerHTML=state.structured?'':days.map(d=>`<p><b>${day(d.day)}</b> · ${d.booked} / ${d.capacity} h <button ${d.helper||((new Date(d.day)-new Date(state.today))/86400000)<2?'disabled':''} onclick="helper('${d.day}')">${d.helper?'Helper booked':'+4 h helper · $100'}</button></p>`).join('');document.getElementById('requests').innerHTML=state.orders.filter(o=>o.status==='Requested').map(req).join('')||'<p class="muted">No requests awaiting acceptance. Fetch today’s DMs.</p>';document.getElementById('blocked').innerHTML=state.orders.filter(o=>o.status==='Incompatible').map(req).join('')||'<p class="muted">No blocked requests.</p>';document.getElementById('messages').innerHTML=state.messages.map(m=>`<details><summary>${day(m.day)} · ${esc(m.customer)} · ${esc(m.outcome)}</summary><pre>${esc(m.body)}</pre></details>`).join('')||'No messages fetched yet.';document.getElementById('audit').innerHTML=state.audit.map(a=>`<p>${day(a.day)} · ${esc(a.detail)}</p>`).join('')||'No activity yet.';drawOrders()}
load();</script></html>'''

def serve(port, db=None, open_browser=False):
    bakery=Bakery(db)
    class Handler(BaseHTTPRequestHandler):
        def reply(self,status,body,kind='application/json'):
            raw=(json.dumps(body) if kind=='application/json' else body).encode('utf-8')
            self.send_response(status); self.send_header('Content-Type',kind+'; charset=utf-8'); self.send_header('Content-Length',str(len(raw))); self.send_header('Cache-Control','no-store'); self.end_headers(); self.wfile.write(raw)
        def do_GET(self):
            if self.path=='/': self.reply(200,PAGE,'text/html')
            elif self.path=='/api/state': self.reply(200,bakery.state())
            elif self.path=='/README.html': self.reply(200,(ROOT/'README.html').read_text(encoding='utf-8'),'text/html')
            else: self.reply(404,{'error':'Not found'})
        def do_POST(self):
            # Local-only app: refuse browser requests from another origin.
            if self.headers.get('Origin') not in (None, f'http://127.0.0.1:{port}',f'http://localhost:{port}'):
                self.reply(403,{'error':'Origin not allowed'}); return
            if self.path!='/api/action': self.reply(404,{'error':'Not found'}); return
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<10000: raise ValueError('Invalid request size')
                data=json.loads(self.rfile.read(size)); message=bakery.action(data['action'],data)
                self.reply(200,{'message':message})
            except (ValueError,KeyError,TypeError) as e: self.reply(400,{'error':str(e)})
    server=HTTPServer(('127.0.0.1',port),Handler)
    print(f'Bakery app running at http://127.0.0.1:{port} (keep this window open).')
    if open_browser: webbrowser.open(f'http://127.0.0.1:{port}')
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close(); bakery.db.close()

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--port',type=int,default=DEFAULT_PORT); parser.add_argument('--db'); parser.add_argument('--open',action='store_true'); args=parser.parse_args()
    serve(args.port,args.db,args.open)
