import importlib.util, io, json, queue, socket, subprocess, tempfile, threading, unittest
from pathlib import Path
from unittest.mock import patch, Mock

source=Path(__file__).with_name('launcher.py')
if not source.exists():source=Path(__file__).resolve().parents[2]/'launcher.py'
spec=importlib.util.spec_from_file_location('launcher_under_test',source)
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)

class Widget:
 def __init__(self,value=''):self.value=value;self.options={}
 def get(self):return self.value
 def configure(self,**kw):self.options.update(kw)
 def itemconfigure(self,*args,**kw):pass
 def insert(self,*args):pass
 def see(self,*args):pass

class Root:
 def __init__(self):self.thread=threading.get_ident();self.callbacks=[];self.destroyed=False
 def after(self,*args):
  if threading.get_ident()!=self.thread:raise AssertionError('Tk called by worker')
  self.callbacks.append(args)
 def destroy(self):self.destroyed=True

class Process:
 def __init__(self):self.pid=12345;self.code=None;self.stdout=io.StringIO('test log\n');self.terminated=0;self.killed=0
 def poll(self):return self.code
 def terminate(self):self.terminated+=1;self.code=0
 def wait(self,timeout=None):return self.code
 def kill(self):self.killed+=1;self.code=-9

def app():
 a=mod.Launcher.__new__(mod.Launcher)
 a.root=Root();a.server=a.admin=a.game=None;a.game_url=None;a.player_kind='flash';a.player_path='flash.exe'
 a.running=False;a._session=0;a._cancel=threading.Event();a._closed=False;a._can_start=True;a._admin_waiting=None
 a.log_q=queue.Queue();a.logs=[];a._log=a.logs.append
 for k in ('play_btn','stop_btn','admin_btn','uin_box','status_lbl','dot','log'):setattr(a,k,Widget())
 a._dot_id=1;a.uin_var=Widget('001')
 return a

class LauncherTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.config=Path(self.tmp.name)/'launcher_config.json'
  self.p=patch.object(mod,'CONFIG_PATH',str(self.config));self.p.start();self.addCleanup(self.p.stop)
 def start(self,a,proc=None):
  proc=proc or Process()
  with patch.object(mod,'port_open',return_value=False),patch.object(mod.subprocess,'Popen',return_value=proc),patch.object(mod.threading,'Thread'):
   a.start()
  return proc
 def test_numeric_accounts_only_and_sqlite_bounds(self):
  for value in ('','abc','1&uin=2','１２','0','9223372036854775808','9'*500,None):self.assertFalse(mod.valid_uin(value))
  for value in ('001','700000200','4294967295'):self.assertTrue(mod.valid_uin(value))
 def test_invalid_account_does_not_spawn_or_save(self):
  a=app();a.uin_var.value='1&uin=2'
  with patch.object(mod.messagebox,'showwarning'),patch.object(mod.subprocess,'Popen') as spawn:a.start()
  spawn.assert_not_called();self.assertFalse(self.config.exists())
 def test_corrupt_config_shapes_are_recovered(self):
  for data in ('[]','null','{bad','{"last_uin":[],"recent":7}'):
   self.config.write_text(data);self.assertIsInstance(mod.load_config(),dict)
 def test_recent_account_list_is_validated_and_deduplicated(self):
  self.config.write_text(json.dumps({'last_uin':'abc','recent':['001','001',None,{},'2','１２']}))
  self.assertEqual(mod.load_config()['recent'],['001','2']);self.assertEqual(mod.load_config()['last_uin'],mod.DEFAULT_UIN)
 def test_config_write_is_atomic_and_preserves_previous_on_failure(self):
  self.assertTrue(mod.save_config({'last_uin':'001'}));before=self.config.read_bytes()
  with patch.object(mod.os,'replace',side_effect=OSError('locked')):self.assertFalse(mod.save_config({'last_uin':'2'}))
  self.assertEqual(self.config.read_bytes(),before);self.assertEqual(list(self.config.parent.glob('*.tmp.*')),[])
 def test_all_four_ports_are_checked_and_unknown_owners_are_not_killed(self):
  for busy in (mod.WEB_PORT,mod.ADMIN_PORT,mod.GAME_PORT,mod.POLICY_PORT):
   a=app()
   with patch.object(mod,'port_open',side_effect=lambda p:p==busy),patch.object(mod.subprocess,'Popen') as spawn,patch.object(mod.messagebox,'showerror'):
    a.start()
   spawn.assert_not_called();self.assertFalse(a.running)
 def test_standalone_admin_is_retired_before_game_start(self):
  a=app();old=Process();a.admin=old;new=self.start(a)
  self.assertEqual(old.terminated,1);self.assertIsNone(a.admin);self.assertIs(a.server,new)
 def test_ready_worker_never_calls_tk(self):
  a=app();proc=self.start(a);generation=a._session
  with patch.object(mod,'port_open',return_value=True),patch.object(mod,'web_ready',return_value=True),patch.object(mod,'admin_ready',return_value=True),patch.object(mod,'policy_ready',return_value=True):
   t=threading.Thread(target=a._wait_ready,args=(proc,generation));t.start();t.join(2)
  self.assertFalse(t.is_alive());a._launch_game=Mock();a._poll_log();a._launch_game.assert_called_once()
 def test_policy_missing_does_not_open_flash_and_timeout_cleans_up(self):
  a=app();proc=self.start(a);a._launch_game=Mock()
  with patch.object(mod,'START_TIMEOUT',0):a._wait_ready(proc,a._session)
  a._poll_log();a._launch_game.assert_not_called();self.assertFalse(a.running);self.assertEqual(proc.terminated,1)
  self.assertEqual(a.play_btn.options['state'],'normal')
 def test_readiness_requires_policy_and_admin(self):
  for absent in ('policy_ready','admin_ready'):
   a=app();proc=self.start(a)
   checks={name:True for name in ('web_ready','admin_ready','policy_ready')};checks[absent]=False
   with patch.object(mod,'START_TIMEOUT',0.01),patch.object(mod,'port_open',return_value=True),patch.object(mod,'web_ready',return_value=checks['web_ready']),patch.object(mod,'admin_ready',return_value=checks['admin_ready']),patch.object(mod,'policy_ready',return_value=checks['policy_ready']):a._wait_ready(proc,a._session)
   event=a.log_q.get_nowait();self.assertEqual(event[2],a._startup_failed)
 def test_old_ready_exit_and_timeout_callbacks_do_not_touch_new_server(self):
  a=app();old=self.start(a);generation=a._session;a.stop();new=self.start(a);a._launch_game=Mock()
  a._post(generation,a._ready,old,generation);a.log_q.put(('__SERVER_EXITED__',generation,old));a._post(generation,a._startup_failed,old,generation)
  a._poll_log();self.assertIs(a.server,new);self.assertTrue(a.running);self.assertEqual(new.terminated,0);a._launch_game.assert_not_called()
 def test_reader_captures_its_process_instead_of_replacement(self):
  a=app();old=Process();new=Process();new.stdout=io.StringIO('new log\n');a.server=new
  a._reader(old,1);events=list(a.log_q.queue)
  self.assertEqual(events[0],('__LOG__',1,'test log\n'));self.assertIs(events[-1][2],old)
 def test_server_crash_closes_owned_game(self):
  a=app();proc=self.start(a);game=Process();a.game=game;proc.code=1
  a.log_q.put(('__SERVER_EXITED__',a._session,proc));a._poll_log()
  self.assertEqual(game.terminated,1);self.assertFalse(a.running);self.assertIsNone(a.game)
 def test_spawn_failure_restores_buttons(self):
  a=app()
  with patch.object(mod,'port_open',return_value=False),patch.object(mod.subprocess,'Popen',side_effect=OSError('missing')):a.start()
  self.assertFalse(a.running);self.assertEqual(a.play_btn.options['state'],'normal');self.assertEqual(a.stop_btn.options['state'],'disabled')
 def test_stopping_cancels_ready_work(self):
  a=app();proc=self.start(a);generation=a._session;a.stop();a._wait_ready(proc,generation)
  self.assertTrue(a.log_q.empty());self.assertTrue(a._cancel.is_set())
 def test_force_stop_waits_for_process_exit(self):
  a=app();proc=Process();proc.terminate=lambda:None
  proc.wait=Mock(side_effect=[subprocess.TimeoutExpired('test',1.5),0])
  self.assertTrue(a._terminate_owned(proc,'test'));self.assertEqual(proc.killed,1);self.assertEqual(proc.wait.call_count,2)
 def test_failed_stop_retains_handle_and_blocks_restart(self):
  a=app();proc=self.start(a);proc.terminate=Mock(side_effect=OSError('denied'))
  self.assertFalse(a.stop());self.assertIs(a.server,proc);self.assertEqual(a.play_btn.options['state'],'disabled')
  with patch.object(mod.subprocess,'Popen') as spawn:a.start()
  spawn.assert_not_called()
 def test_repeated_open_admin_does_not_spawn_duplicate(self):
  a=app();proc=Process()
  with patch.object(mod,'admin_ready',return_value=False),patch.object(mod,'port_open',return_value=False),patch.object(mod.os.path,'isfile',return_value=True),patch.object(mod.subprocess,'Popen',return_value=proc) as spawn,patch.object(mod.threading,'Thread'):
   a.open_admin();a.open_admin()
  spawn.assert_called_once();self.assertIs(a.admin,proc)
 def test_admin_worker_uses_main_thread_queue(self):
  a=app();proc=Process();a.admin=proc
  with patch.object(mod,'admin_ready',return_value=True):
   t=threading.Thread(target=a._wait_admin_and_open,args=(proc,0));t.start();t.join(2)
  with patch.object(mod.webbrowser,'open') as browser:a._poll_log()
  browser.assert_called_once()
 def test_late_admin_event_after_close_cannot_open_browser(self):
  a=app();old=Process();a.admin=old;generation=a._session;a.on_close()
  a._post(generation,a._admin_opened,old,generation)
  with patch.object(mod.webbrowser,'open') as browser:a._poll_log()
  browser.assert_not_called();self.assertTrue(a.root.destroyed)
 def test_heavy_logs_are_bounded_per_ui_pass(self):
  a=app()
  for i in range(350):a.log_q.put(('__LOG__',0,str(i)))
  a._poll_log();self.assertEqual(len(a.logs),200);self.assertEqual(a.log_q.qsize(),150)
 def test_log_is_persisted_and_rotated(self):
  a=app();log=Path(self.tmp.name)/'portable_launcher.log';log.write_bytes(b'x'*(5*1024*1024+1))
  with patch.object(mod,'LAUNCHER_LOG',str(log)):mod.Launcher._log(a,'test message')
  self.assertIn('test message',log.read_text());self.assertTrue(Path(str(log)+'.1').is_file())
 def test_console_interpreter_or_portable_runtime_is_used(self):
  with patch.object(mod.sys,'_pvz_runtime','runtime.exe',create=True):self.assertEqual(mod.python_command('path/gm.py','001'),['runtime.exe','--script','gm.py','001'])
  with patch.object(mod.sys,'_pvz_runtime',None,create=True),patch.object(mod.sys,'executable',r'C:\Python\pythonw.exe'),patch.object(mod.os.path,'isfile',side_effect=lambda p:p.endswith('python.exe')):
   self.assertEqual(mod.python_command('gm.py')[0],r'C:\Python\python.exe')
 def test_save_mutations_are_blocked_while_game_is_running(self):
  a=app();a.running=True
  with patch.object(mod.subprocess,'run') as run:
   ok,reason=a._run_gm(['001','coin','5'])
  self.assertFalse(ok);run.assert_not_called();self.assertIn('停止',reason)
  a.running=False
  with patch.object(mod,'port_open',return_value=True),patch.object(mod.subprocess,'run') as run:
   self.assertFalse(a._run_gm(['001','coin','5'])[0])
  run.assert_not_called()
 def test_callback_failure_does_not_stop_main_thread_polling(self):
  a=app();a._post(0,Mock(side_effect=OSError('browser failed')));a._poll_log()
  self.assertTrue(a.root.callbacks);self.assertIn('操作失败',a.logs[0])

class NetworkTests(unittest.TestCase):
 def responder(self,response):
  server=socket.socket();server.bind(('127.0.0.1',0));server.listen();port=server.getsockname()[1]
  def run():
   try:
    with server:
     con,_=server.accept()
     with con:con.recv(4096);con.sendall(response)
   except OSError:pass
  t=threading.Thread(target=run,daemon=True);t.start();return port,t
 def test_loader_http_identity_and_404(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);(root/'webroot').mkdir();signature=b'CWS\x20\x01\x00\x00\x00';(root/'webroot/loader_p.swf').write_bytes(signature+b'data')
   for body,status,expected in ((signature,b'200 OK',True),(b'<html>no',b'200 OK',False),(signature,b'404 Missing',False)):
    port,t=self.responder(b'HTTP/1.0 '+status+b'\r\nContent-Length: 8\r\n\r\n'+body)
    with patch.object(mod,'HERE',tmp),patch.object(mod,'WEB_PORT',port):self.assertEqual(mod.web_ready('loader_p.swf'),expected)
    t.join(2)
 def test_policy_must_allow_game_port(self):
  for ports,expected in (('1-65535',True),('9001',True),('80,9001',True),('80',False),('bad',False)):
   port,t=self.responder(('<cross-domain-policy><allow-access-from domain="*" to-ports="'+ports+'"/></cross-domain-policy>\x00').encode())
   with patch.object(mod,'POLICY_PORT',port):self.assertEqual(mod.policy_ready(),expected)
   t.join(2)

if __name__=='__main__':unittest.main(verbosity=2)
