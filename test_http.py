"""HTTP smoke checks on temporary databases, with child servers cleaned up."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parent
with tempfile.TemporaryDirectory() as tmp:
    for folder, port in [('App1_Basic', 18011), ('App2_Structured', 18012)]:
        process = subprocess.Popen([sys.executable, str(ROOT/folder/'app.py'), '--port', str(port), '--db', str(Path(tmp)/(folder+'.db'))], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            base = f'http://127.0.0.1:{port}'
            for retry in range(30):
                try:
                    with urllib.request.urlopen(base+'/', timeout=2) as r:
                        assert r.status == 200 and b'<title>Butter & Batch' in r.read()
                    break
                except OSError:
                    if retry == 29: raise
                    time.sleep(.1)
            assert urllib.request.urlopen(base+'/README.html').status == 200
            s = json.load(urllib.request.urlopen(base+'/api/state'))
            assert len(s['orders']) == 13 and s['today'] == '2026-11-23'
            req = urllib.request.Request(base+'/api/action', data=json.dumps({'action':'fetch'}).encode(), headers={'Content-Type':'application/json'})
            assert 'Fetched 3' in json.load(urllib.request.urlopen(req))['message']
            assert len(json.load(urllib.request.urlopen(base+'/api/state'))['messages']) == 3
            print(folder+': page, README, initial data and message fetch passed')
        finally:
            process.terminate()
            process.wait(timeout=10)
