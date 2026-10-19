"""Normal Mac app entry point. Starts the local service without a Terminal window."""
import json
from pathlib import Path
import subprocess
import sys
import time
import os
import signal
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent
URL = 'http://127.0.0.1:8767'


def ready():
    try:
        with urlopen(URL+'/api/status', timeout=1) as response:
            data=json.load(response)
        return data.get('app') == 'academic-assistant' and data.get('version') == 48
    except Exception:
        return False


def main(open_browser=True):
    if not ready():
        # Replace only this app's idle older service, after verifying both the
        # API identity and its working directory. Never kill an active update.
        try:
            with urlopen(URL+'/api/status',timeout=1) as response:old=json.load(response)
            if old.get('app')=='academic-assistant' and old.get('version',0)<48:
                if old.get('status') in ('running','connecting'):
                    if open_browser:subprocess.run(['/usr/bin/open','-a','Google Chrome',URL],check=True)
                    return 0
                pids=subprocess.check_output(['/usr/sbin/lsof','-t','-iTCP:8767','-sTCP:LISTEN'],text=True).split()
                for pid in pids:
                    cwd=subprocess.check_output(['/usr/sbin/lsof','-a','-p',pid,'-d','cwd','-Fn'],text=True)
                    if 'n'+str(ROOT)+'\n' in cwd:os.kill(int(pid),signal.SIGTERM)
                time.sleep(.5)
        except Exception:
            pass
        runtime=ROOT/'.runtime'
        runtime.mkdir(exist_ok=True, mode=0o700)
        with (runtime/'server.log').open('a') as log:
            subprocess.Popen([sys.executable,str(ROOT/'server.py')],cwd=ROOT,
                             stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
        for _ in range(40):
            if ready():
                break
            time.sleep(.25)
        else:
            subprocess.run(['/usr/bin/osascript','-e','display alert "Coursewell could not start" message "Please reopen the app. If it still fails, share the error with your assistant. Your course files are safe."'],check=False)
            return 1
    # Explicitly target regular Chrome; the system URL handler may point to
    # the separate testing browser used for Moodle authentication.
    if open_browser:subprocess.run(['/usr/bin/open','-a','Google Chrome',URL],check=True)
    return 0


if __name__=='__main__':
    sys.exit(main(open_browser='--background' not in sys.argv))
