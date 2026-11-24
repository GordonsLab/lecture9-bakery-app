"""Regression tests use isolated temporary databases, never user data."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parent

def module(folder):
    spec=importlib.util.spec_from_file_location(folder,ROOT/folder/'app.py')
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

class Rules(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.apps=[module(n).Bakery(Path(self.temp.name)/(n+'.db')) for n in ('App1_Basic','App2_Structured')]
    def tearDown(self):
        for a in self.apps: a.db.close()
        self.temp.cleanup()
    def order(self,a,name): return a.db.execute('SELECT * FROM orders WHERE customer=?',(name,)).fetchone()
    def test_import_and_idempotence(self):
        for a in self.apps:
            self.assertEqual(len(a.state()['orders']),13)
            a.action('fetch',{}); a.action('fetch',{})
            self.assertEqual(len(a.state()['messages']),3)
            self.assertEqual(self.order(a,'Okafor')['status'],'Incompatible')
            self.assertEqual(self.order(a,'Jasmine')['status'],'Requested')
            with self.assertRaises(ValueError): a.action('helper',{'day':'2026-11-24'})
            a.action('helper',{'day':'2026-11-25'})
            with self.assertRaises(ValueError): a.action('helper',{'day':'2026-11-25'})
            self.assertEqual(a.capacity('2026-11-25'),12)
    def test_week_accept_all_without_helper(self):
        for a in self.apps:
            for i in range(7):
                a.action('fetch',{})
                if i==1: self.assertEqual(self.order(a,'Brown')['status'],'Cancelled')
                if i==2: self.assertEqual(self.order(a,'Moreau')['quantity'],6)
                if i==3: self.assertIn('Change blocked',a.db.execute("SELECT outcome FROM messages WHERE customer='Lam' AND day='2026-11-26'").fetchone()[0])
                a.action('next',{})
                for d in a.state()['days']: self.assertLessEqual(d['booked'],d['capacity'])
            self.assertEqual(len(a.state()['messages']),25)
            self.assertEqual(self.order(a,'Costa')['status'],'Cancelled')
            self.assertEqual(self.order(a,'Zhou')['quantity'],4)
            self.assertEqual(self.order(a,'Lee')['status'],'Accepted')
            self.assertEqual(self.order(a,'Wilson')['status'],'Incompatible')
            self.assertEqual(self.order(a,'Novak')['status'],'Picked up')
            self.assertEqual(a.today,'2026-11-30')
            self.assertEqual(a.state()['committed'],3640)
            self.assertEqual(a.state()['realized'],3000)
    def test_helper_recovers_capacity_and_rechecks_sequentially(self):
        for a in self.apps:
            for _ in range(3): a.action('next',{})
            a.action('fetch',{})
            self.assertEqual(self.order(a,'Wilson')['status'],'Incompatible')
            a.action('helper',{'day':'2026-11-28'})
            self.assertEqual(self.order(a,'Wilson')['status'],'Requested')
            a.action('next',{})
            self.assertEqual(self.order(a,'Wilson')['status'],'Accepted')
            self.assertEqual(self.order(a,'Novak')['status'],'Incompatible')
            self.assertEqual(self.order(a,'Bell')['status'],'Incompatible')
            self.assertEqual(a.booked('2026-11-28'),12)
            self.assertEqual(a.state()['costs'],100)
    def test_persistence_and_rejection(self):
        for a in self.apps:
            a.action('fetch',{}); o=self.order(a,'Jasmine')
            a.action('reject',{'id':o['id']})
            with self.assertRaises(ValueError): a.action('accept',{'id':o['id']})
            self.assertEqual(self.order(a,'Jasmine')['status'],'Rejected')
            a.action('next',{})
            self.assertEqual(self.order(a,'Kim')['status'],'Picked up')
            self.assertEqual(a.state()['realized'],280)
            path=a.db.execute('PRAGMA database_list').fetchone()[2]
            b=type(a)(path)
            self.assertEqual(b.today,'2026-11-24')
            self.assertEqual(self.order(b,'Jasmine')['status'],'Rejected')
            b.db.close()
    def test_invalid_change_preserves_original(self):
        for a in self.apps:
            a.action('next',{}); a.action('next',{})
            with a.db:
                a.db.execute("UPDATE orders SET quantity=6 WHERE customer='Haddad'")
            # Saturday has no room for Moreau's requested increase.
            a.action('fetch',{})
            self.assertEqual(self.order(a,'Moreau')['quantity'],4)
            self.assertIn('Change blocked',a.db.execute("SELECT outcome FROM messages WHERE customer='Moreau'").fetchone()[0])
    def test_person_schedules_cover_each_order_without_overlap(self):
        for a in self.apps[1:]:
            for _ in range(3): a.action('next',{})
            a.action('helper',{'day':'2026-11-28'})
            a.action('next',{})
            plans=a.schedules()
            counts={}
            for p in plans:
                self.assertLessEqual(p['hours'],p['limit'])
                end='00:00'
                for t in p['tasks']:
                    self.assertGreaterEqual(t['start'],end)
                    self.assertLessEqual(t['end'],'09:00' if p['person']=='Helper' else '17:00')
                    self.assertGreater(t['quantity'],0)
                    self.assertIsInstance(t['quantity'],int)
                    end=t['end']
                    counts[t['order']]=counts.get(t['order'],0)+t['quantity']
            for o in a.state()['orders']:
                if o['status'] in ('Accepted','Picked up'): self.assertEqual(counts[o['id']],o['quantity'])
                else: self.assertNotIn(o['id'],counts)
            self.assertEqual(sum(p['hours'] for p in plans if p['day']=='2026-11-28'),12)
    def test_undo_restores_full_state_and_survives_noop_or_error(self):
        for a in self.apps:
            initial=a.state()
            a.action('fetch',{})
            fetched=a.state()
            a.action('fetch',{}) # no duplicate imports and no replacement undo point
            with self.assertRaises(ValueError): a.action('helper',{'day':'2026-11-24'})
            a.action('undo',{})
            self.assertEqual(a.state(),initial)
            with self.assertRaises(ValueError): a.action('undo',{})
            a.action('fetch',{})
            for action,data in [('accept',{'id':self.order(a,'Jasmine')['id']}),('reject',{'id':self.order(a,'Patel')['id']}),('helper',{'day':'2026-11-28'}),('next',{})]:
                before=a.state()
                a.action(action,data)
                a.action('undo',{})
                after=a.state()
                for key in before:
                    if key!='undoLabel': self.assertEqual(before[key],after[key])

    def test_schedules_and_gantt_are_final_build_only(self):
        basic=module('App1_Basic')
        final=module('App2_Structured')
        self.assertNotIn('Gantt chart',basic.PAGE)
        self.assertNotIn('Baker schedule',basic.PAGE)
        self.assertNotIn('schedules',self.apps[0].state())
        self.assertIn('Gantt chart',final.PAGE)
        self.assertIn('Baker schedule',final.PAGE)
        self.assertIn('schedules',self.apps[1].state())

if __name__=='__main__': unittest.main(verbosity=2)

