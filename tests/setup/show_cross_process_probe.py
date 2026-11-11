"""Cross-process application probe; fixture controller, no provider/TTS or owner approval."""
import json, os, subprocess, sys, tempfile, time
from pathlib import Path
from types import SimpleNamespace
from onair import show_runtime as sr

ROOT=Path(__file__).resolve().parent
OWNER=r'''
import os,sys,time,threading,json
from pathlib import Path
from onair import show_runtime as sr
class NoModel:
 def __getattr__(self, name): raise RuntimeError('model deliberately unavailable in offline control fixture')
d=Path(sys.argv[1]); body={'agent_name':'Fixture','host_name':'Host','brief':'fixture','quiet_s':600,'voice':False,'policy':'review','style':{},'never_say':[],'model_label':'fixture unavailable'}
s=sr.AgentShow(body=body,persona='fixture',model_client=NoModel(),tts_dispatch=None,data_dir=d)
s.thread=threading.Thread(target=s._thread_main,daemon=True);s.thread.start()
if not s._ready.wait(15) or s._error: raise RuntimeError('owner show did not start: '+str(s._error))
sr._SHOWS[os.fspath(d)]=s
sr._write_private(d/sr.TOKEN_FILE,s.token+'\n')
sr._write_private(d/sr.SHOW_FILE,json.dumps({'url':s.url,'pid':os.getpid(),'overlay_url':s.overlay.overlay_url,'token_sha':sr._sha(s.token),'digest':'fixture','approved_via':'fixture','started':time.time()}))
print('READY',flush=True)
try: sys.stdin.read()
finally: s.end()
'''
with tempfile.TemporaryDirectory() as tmp:
 d=Path(tmp)
 env=dict(os.environ);env['PYTHONDONTWRITEBYTECODE']='1';env['HERMES_DISABLE_LAZY_INSTALLS']='1'
 p=subprocess.Popen([sys.executable,'-B','-c',OWNER,str(d)],env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
 try:
  import select
  ready,_,_=select.select([p.stdout],[],[],20)
  if not ready: raise RuntimeError('owner readiness timeout')
  line=p.stdout.readline().strip()
  if line!='READY': raise RuntimeError('owner startup failed: '+line+' '+p.stderr.read())
  eng=SimpleNamespace(dir=d,model_client=None,tts_dispatch=None,_read=lambda n:sr._read(d/n))
  st=sr.status(eng)
  print('direct',sr.call(d,'GET','/facts'),flush=True)
  assert st['running'] is True, st
  stop=sr.control(eng,'stop'); assert stop['ok'],stop
  after=sr.status(eng); assert after['running'] and after['show']['mode']=='paused',after
  record=json.loads((d/sr.SHOW_FILE).read_text()); original=(d/sr.SHOW_FILE).read_text()
  record['pid']=os.getpid()  # alive but foreign consumer, not the actual show owner
  (d/sr.SHOW_FILE).write_text(json.dumps(record))
  assert sr._running_record(eng) is not None, 'uncertain record must block a second show'
  result=sr.call(d,'GET','/facts')
  bad_resume=sr.call(d,'POST','/resume')
  unknown=sr.status(eng)
  assert unknown.get('state')=='unknown' and unknown.get('running') is None, unknown
  record['pid']=99999999
  (d/sr.SHOW_FILE).write_text(json.dumps(record))
  assert sr._running_record(eng) is not None, 'stale record is unknown, not permission to start another show'
  (d/sr.SHOW_FILE).write_text(original)
  unchanged=sr.status(eng)
  print(json.dumps({'positive_running':st['running'],'stop_mode':after['show']['mode'],'foreign_live_pid_http':result[0],'foreign_resume_http':bad_resume[0],'after_foreign_resume':unchanged['show']['mode'],'scope':'actual cross-process show control; fixture model unavailable; no provider or TTS'}))
  assert result[0]!=200 and bad_resume[0]!=200 and unchanged['show']['mode']=='paused','foreign live PID accepted'
  resumed=sr.control(eng,'resume'); assert resumed['ok'],resumed
  assert sr.status(eng)['show']['mode'] != 'paused'
  ended=sr.control(eng,'end'); assert ended['ok'],ended
  assert not sr.status(eng)['running']
 finally:
  if p.poll() is None:
   p.stdin.close()
   try: p.wait(timeout=10)
   except subprocess.TimeoutExpired: p.kill(); p.wait()
