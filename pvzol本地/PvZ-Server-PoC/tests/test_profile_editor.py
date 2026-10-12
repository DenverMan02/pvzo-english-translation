import json, os, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import player_store as store
import profile_editor as profile

class ProfileTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
  old=store.DB_PATH;store.set_db_path(str(self.root/'players.db'));store.init_db()
  self.addCleanup(self.tmp.cleanup);self.addCleanup(lambda:store.set_db_path(old))
  self.uin=700001234;self.player=store.get_or_create_player(self.uin)[0]
  self.heads=self.root/'webroot/image/head';self.heads.mkdir(parents=True)
  self.image=Path(r'D:\pvzol本地最新最新ol\pvzol本地\webroot\image\head\2.png').read_bytes()
  for name in ('unknow.png','1.png','2.png'):(self.heads/name).write_bytes(self.image)
 def read(self):
  with store._db() as con:return dict(con.execute('SELECT * FROM players WHERE uin=?',(self.uin,)).fetchone())
 def save(self,**kw):return profile.save_profile(self.uin,kw,self.root)
 def test_signature_is_available_before_importing_game_server(self):
  self.assertIn('signature',store.DEFAULTS);self.assertEqual(self.read()['signature'],'')
 def test_existing_schema_gains_signature_without_losing_name(self):
  import sqlite3
  legacy=self.root/'legacy.db'
  with sqlite3.connect(legacy) as con:
   con.execute('CREATE TABLE players(uin INTEGER PRIMARY KEY,name TEXT)')
   con.execute('INSERT INTO players VALUES(?,?)',(self.uin,'旧名字'))
  con.close()
  store.set_db_path(str(legacy));store.init_db()
  self.assertEqual(self.read()['signature'],'');self.assertEqual(self.read()['name'],'旧名字')
 def test_name_signature_and_builtin_avatar_persist_together(self):
  before=self.read();result=self.save(name='星辰二',signature='我的新签名',avatar=2);after=self.read()
  self.assertEqual(result,{'name':'星辰二','signature':'我的新签名','face_id':2,'qq_face_url':''})
  for field in ('coin','sun','ticket','present_ticket','level','sex'):
   if field in before:self.assertEqual(before[field],after[field])
 def test_legacy_long_generated_name_can_keep_name_while_editing_signature(self):
  self.save(signature='保留旧名称',avatar=1);self.assertEqual(self.read()['name'],self.player['name'])
 def test_numeric_name_is_text_and_signature_can_be_cleared(self):
  self.save(name='001234',signature='旧签名');self.save(signature='');self.assertEqual(self.read()['name'],'001234');self.assertEqual(self.read()['signature'],'')
 def test_utf16_limits_match_original_client(self):
  self.save(name='😀甲乙',signature='😀'*15)
  for fields in ({'name':'甲'},{'name':'甲'*8},{'signature':'😀'*16},{'signature':'换\n行'},{'name':'甲\t乙'}):
   before=self.read()
   with self.assertRaises(ValueError):self.save(**fields)
   self.assertEqual(self.read(),before)
 def test_duplicate_name_is_rejected_without_partial_profile(self):
  store.get_or_create_player(2);store.save_player(2,name='已有名字')
  before=self.read()
  with self.assertRaises(ValueError):self.save(name='已有名字',signature='不该保存',avatar=2)
  self.assertEqual(self.read(),before)
 def test_custom_png_is_copied_to_served_local_url(self):
  source=self.root/'测试 图片.png';source.write_bytes(self.image)
  result=self.save(name='星辰二',signature='图片头像',avatar='custom',image_path=str(source))
  target=self.root/'webroot'/result['qq_face_url'].split('8080/',1)[1]
  self.assertEqual(target.read_bytes(),self.image);self.assertEqual(self.read()['qq_face_url'],result['qq_face_url'])
  source.unlink();self.assertTrue(target.exists())
 def test_reset_avatar_clears_custom_override(self):
  source=self.root/'a.png';source.write_bytes(self.image)
  self.save(avatar='custom',image_path=str(source));self.save(avatar=0)
  self.assertEqual((self.read()['face_id'],self.read()['qq_face_url']),(0,''))
 def test_invalid_avatar_and_image_are_rejected_without_changes(self):
  source=self.root/'fake.png';source.write_text('not an image')
  for fields in ({'avatar':999},{'avatar':True},{'avatar':'custom','image_path':str(source)},{'avatar':'custom'}):
   before=self.read()
   with self.assertRaises(ValueError):self.save(**fields)
   self.assertEqual(self.read(),before)
 def test_cached_friend_and_guild_names_follow_rename(self):
  store.get_or_create_player(2);store.add_friend(2,self.uin,'旧名称',1200,1,0,0,0,0)
  with store._db() as con:
   con.execute('CREATE TABLE local_guild_members(uin INTEGER PRIMARY KEY,name TEXT)')
   con.execute('INSERT INTO local_guild_members VALUES(?,?)',(self.uin,'旧名称'))
  self.save(name='新名称')
  self.assertEqual(store.get_friend_by_role_id(2,self.uin)['friend_name'],'新名称')
  with store._db() as con:self.assertEqual(con.execute('SELECT name FROM local_guild_members').fetchone()[0],'新名称')
 def test_transaction_failure_rolls_back_fields_and_new_image(self):
  source=self.root/'a.png';source.write_bytes(self.image);before=self.read()
  real=store.save_player
  def fail(uin,**fields):real(uin,**fields);raise RuntimeError('injected failure')
  with patch.object(store,'save_player',side_effect=fail):
   with self.assertRaises(RuntimeError):self.save(name='新名称',avatar='custom',image_path=str(source))
  self.assertEqual(self.read(),before);self.assertEqual(list((self.heads/'custom').glob('*')),[])
 def test_profile_is_keyed_by_uin_not_assumed_role_id(self):
  store.save_player(self.uin,role_id=999999)
  self.save(name='新名称',signature='账号一致');self.assertEqual(self.read()['name'],'新名称')
 def test_login_city_and_friend_messages_use_saved_profile(self):
  import pvz_poc_server as srv
  srv._CLIENTDB_PATH=r'D:\pvzol本地最新最新ol\pvzol本地\webroot\clientDB.txt'
  self.save(name='星辰二',signature='同步签名',avatar=2)
  player=store.get_player_by_role_id(self.uin)
  init=srv.make_init_sc(player)
  self.assertEqual(srv.parse_string_field(init,1,''),'星辰二');self.assertEqual(srv.parse_varint_fields(init)[13],2)
  self.assertEqual(srv.parse_string_field(init,23,''),'http://127.0.0.1:8080/image/head/2.png')
  with patch.object(srv.guild_local,'member_index',return_value={}):city=srv.handle_worldmap_getcityinfo(b'',player)
  self.assertEqual(srv.parse_string_field(city,2,''),'星辰二');self.assertEqual(srv.parse_string_field(city,11,''),'同步签名')
  friend=srv.build_friend_info_dto_from_player(player)
  self.assertEqual(srv.parse_string_field(friend,4,''),'星辰二');self.assertEqual(srv.parse_varint_fields(friend)[5],2)
  self.assertEqual(srv.parse_string_field(friend,14,''),srv.player_face_url(player))

if __name__=='__main__':unittest.main(verbosity=2)

