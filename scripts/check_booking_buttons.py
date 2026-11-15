"""Exercise booking-page buttons in a sandboxed headless browser with a mocked API.

This checks browser JavaScript and DOM behavior, not the real Python HTTP backend.
Generated Python is parsed as text and is never imported or run on the host.
"""
import argparse
import html
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from collab.ui_check import embedded_html


MOCK = r'''<script>
window.uiErrors=[];
window.addEventListener('error',e=>window.uiErrors.push(e.message));
window.uiCalls=[];
let uiBookings=[],uiSequence=0;
window.fetch=async function(path,options={}){
  const url=new URL(path,'http://fixture.local');
  window.uiCalls.push(url.pathname);
  let data,status=200;
  const body=options.body?JSON.parse(options.body):{};
  if(url.pathname==='/api/rooms') data={rooms:['Boardroom A','Boardroom B','Meeting Room 1','Meeting Room 2','Meeting Room 3','Training Room']};
  else if(url.pathname==='/api/bookings') data={bookings:uiBookings.filter(b=>b.status==='active')};
  else if(url.pathname==='/api/availability'){
    const conflicts=uiBookings.filter(b=>b.status==='active');
    data={available:conflicts.length===0,conflicts};
  } else if(url.pathname==='/api/book'){
    if(uiBookings.some(b=>b.status==='active')) {status=409;data={error:'Conflict: room already booked'};}
    else {const booking={...body,id:'ui-booking-'+(++uiSequence),status:'active'};uiBookings.push(booking);status=201;data={booking};}
  } else if(url.pathname==='/api/cancel'){
    const booking=uiBookings.find(b=>b.id===body.id);
    if(!booking) throw Error('Cancel sent an incorrect booking ID');
    booking.status='cancelled';data={cancelled:booking};
  } else throw Error('Unexpected API path: '+url.pathname);
  const snapshot=JSON.parse(JSON.stringify(data));
  return {ok:status<400,status,json:async()=>snapshot};
};
</script>'''


EXERCISE = r'''<script>
window.addEventListener('load',async()=>{
 const report={mocked_api:true,checks:[],errors:[]};
 const until=async fn=>{for(let i=0;i<80;i++){if(fn())return;await new Promise(r=>setTimeout(r,10));}throw Error('Timed out waiting for UI update');};
 const check=(label,ok)=>{if(!ok)throw Error(label);report.checks.push(label);};
 try{
  await until(()=>document.getElementById('bkRoom').options.length===6 && document.querySelector('#bkList table'));
  check('rooms loaded into browser controls',true);
  const availability=document.querySelector('button[onclick="checkAvail()"]');
  const book=document.querySelector('button[onclick="createBooking()"]');
  check('buttons present',Boolean(availability&&book));
  availability.click();
  await until(()=>document.getElementById('avResult').textContent==='Available');
  check('availability button shows available',true);
  book.click();
  await until(()=>document.getElementById('bkResult').textContent.startsWith('Booked:') && document.querySelector('#bkList button'));
  check('book button displays confirmation and cancellation control',true);
  book.click();
  await until(()=>document.getElementById('bkResult').className==='err');
  check('duplicate booking displays conflict',document.getElementById('bkResult').textContent.includes('Conflict'));
  document.querySelector('#bkList button').click();
  await until(()=>!document.querySelector('#bkList button'));
  check('cancel button submits the correct ID and updates the list',window.uiCalls.includes('/api/cancel'));
  document.getElementById('avResult').textContent='';
  availability.click();
  await until(()=>document.getElementById('avResult').textContent==='Available');
  check('cancelled slot becomes available',true);
  book.click();
  await until(()=>document.getElementById('bkResult').textContent.startsWith('Booked:') && document.querySelector('#bkList button'));
  check('same slot can be booked again',true);
  check('no browser JavaScript errors',window.uiErrors.length===0);
  document.body.dataset.uiResult='passed';
 }catch(e){report.errors.push(e.message,...window.uiErrors);document.body.dataset.uiResult='failed';}
 const output=document.createElement('pre');output.id='ui-check-report';output.textContent=JSON.stringify(report);document.body.appendChild(output);
});
</script>'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', required=True)
    args = parser.parse_args()
    source = embedded_html(Path(args.app).read_text(encoding='utf-8'))
    if source is None:
        raise ValueError('Expected a literal INDEX_HTML in the Python source')
    browser = next((p for p in [shutil.which('google-chrome'), shutil.which('chromium'),
                     r'C:\Program Files\Google\Chrome\Application\chrome.exe',
                     r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'] if p and Path(p).is_file()), None)
    if not browser:
        raise ValueError('Chrome/Chromium/Edge is required for the browser UI check')
    directory = BASE / 'runtime' / 'ui-checks'
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=directory) as temp:
        page = Path(temp) / 'fixture.html'
        document = source.replace('<head>', '<head>' + MOCK, 1).replace('</body>', EXERCISE + '</body>', 1)
        page.write_text(document, encoding='utf-8')
        argv = [browser, '--headless=new', '--disable-gpu', '--no-first-run', '--disable-background-networking',
                '--disable-extensions', '--no-default-browser-check', '--virtual-time-budget=3000',
                '--user-data-dir=' + str(Path(temp) / 'profile'), '--dump-dom', page.resolve().as_uri()]
        process = subprocess.run(argv, capture_output=True, timeout=30, shell=False)
        output = process.stdout.decode('utf-8', errors='replace')
        match = re.search(r'<pre id="ui-check-report">(.*?)</pre>', output, re.S)
        if not match:
            print(json.dumps({'passed': False, 'reason': 'Browser did not return a UI report', 'browser_exit_code': process.returncode}))
            return 1
        report = json.loads(html.unescape(match.group(1)))
        report['passed'] = bool(re.search(r'<body[^>]*data-ui-result="passed"', output))
        print(json.dumps(report, ensure_ascii=False))
        return 0 if report['passed'] else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({'passed': False, 'error': type(exc).__name__}))
        raise SystemExit(1)
