"""Real loopback TWeb/XOR7 socket checks using synthetic new/returning users."""
import asyncio
import importlib
import logging
from pathlib import Path
import sqlite3
from contextlib import closing
import unittest
from unittest.mock import patch
from worldmap_test_support import IsolatedServer, many, one


class WorldMapTCPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.qa=IsolatedServer();self.g=self.qa.game;self.wm=importlib.import_module('worldmap_local')
        self.now=1800000000;self.clock=patch.object(self.wm,'_now',side_effect=lambda:self.now);self.clock.start()
        self.tasks=set();self.writers=[];self.records=[]
        parent=self
        class Capture(logging.Handler):
            def emit(self,record):parent.records.append(record.getMessage())
        self.capture=Capture();self.g.log.addHandler(self.capture)
        async def accept(reader,writer):
            task=asyncio.current_task();self.tasks.add(task)
            try:await self.g.handle_client(reader,writer)
            finally:self.tasks.discard(task)
        self.server=await asyncio.start_server(accept,'127.0.0.1',0)
        self.port=self.server.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        for writer in self.writers:
            writer.close();await writer.wait_closed()
        self.server.close();await self.server.wait_closed()
        if self.tasks:
            done,pending=await asyncio.wait(self.tasks,timeout=2)
            for task in pending:task.cancel()
            await asyncio.gather(*pending,return_exceptions=True)
        self.g.log.removeHandler(self.capture);self.clock.stop();self.qa.close()

    async def connect(self,uin):
        reader,writer=await asyncio.open_connection('127.0.0.1',self.port)
        self.writers.append(writer);return reader,writer,uin

    async def send(self,conn,command,data=b''):
        reader,writer,uin=conn
        body=self.g.xor7(self.g.build_cmdcommon('PVZ.Cmd.Cmd_'+command+'_CS',data))
        key=b'qa-only\0';user=uin.to_bytes(4,'big')+len(key).to_bytes(4,'big')+key
        frame=bytes([202])+(12+len(user)+len(body)).to_bytes(4,'big')+len(body).to_bytes(4,'big')+bytes([1,2,1])+user+body
        writer.write(frame);await writer.drain()

    async def until(self,conn,command,timeout=4):
        async def read():
            while True:
                header=await conn[0].readexactly(12)
                self.assertEqual(header[0],202)
                length=int.from_bytes(header[1:5],'big')
                body=await conn[0].readexactly(length-12)
                parsed=self.g.parse_cmdcommon(body)
                if parsed['cmdName']=='PVZ.Cmd.Cmd_'+command+'_SC':return parsed['cmdData']
        return await asyncio.wait_for(read(),timeout)

    async def login(self,uin,new=False):
        conn=await self.connect(uin)
        await self.send(conn,'CheckOldUser');check=await self.until(conn,'CheckOldUser')
        self.assertEqual(one(check,1),0 if new else 1)
        if new:
            await self.send(conn,'SetRoleName',self.g.pb_string(1,'Fresh Cloud QA'))
            body=await self.until(conn,'SetRoleName');self.assertEqual(one(body,1),b'Fresh Cloud QA')
        await self.send(conn,'Init');body=await self.until(conn,'Init')
        self.assertEqual(one(body,9),uin)
        return conn

    async def ready_declaration(self,conn):
        await self.send(conn,'War_DeclareWar',self.g.pb_int64_field(1,self.wm.NPCS[0]['role_id']))
        declared=await self.until(conn,'War_DeclareWar')
        self.now=max(self.now,one(declared,2)+2)

    async def test_new_player_login_map_and_malformed_request_no_disconnect(self):
        conn=await self.login(99100001,new=True)
        await self.send(conn,'City_Init');init=await self.until(conn,'City_Init')
        self.assertEqual(one(init,2),1);self.assertIsInstance(one(init,1),bytes)
        await self.send(conn,'WorldMap_GetInfo',self.g.pb_varint_field(5,1))
        cells=many(await self.until(conn,'WorldMap_GetInfo'),1)
        self.assertGreater(len(cells),1);self.assertTrue(all(one(c,1) in (1,2,3) for c in cells))
        await self.send(conn,'War_SetOut',b'\x80');error=await self.until(conn,'LogicError')
        self.assertIsInstance(one(error,1),int)
        await self.send(conn,'City_Init');self.assertEqual(one(await self.until(conn,'City_Init'),2),1)
        self.assertFalse(any('crashed' in x or 'timer failed' in x for x in self.records),self.records)

    async def test_returning_player_dispatch_idle_timer_reward_return_and_reconnect(self):
        player=self.qa.player(99100002,role_created=1,level=100,coin=9000)
        conn=await self.login(player['uin'])
        v=self.g.pb_varint_field;m=self.g.pb_bytes
        await self.ready_declaration(conn)
        await self.send(conn,'War_SetOut',v(1,2)+m(2,v(1,24)+v(2,18))+v(3,1))
        self.assertEqual(one(await self.until(conn,'War_SetOut'),1),1)
        first=one(await self.until(conn,'War_SyncHistory'),1)
        self.assertEqual(one(first,8),self.wm.MARCHING)
        with closing(sqlite3.connect(self.qa.store.DB_PATH)) as db, db:
            db.row_factory=sqlite3.Row
            row=dict(db.execute('SELECT * FROM local_worldmap_missions WHERE uin=?',(player['uin'],)).fetchone())
        self.now=row['arrive_at']
        arrived=one(await self.until(conn,'War_SyncHistory',timeout=4),1)
        self.assertEqual(one(arrived,8),self.wm.RETURNING,'must push while client sends no request')
        expected=9000+(row['reward'] if row['won'] else 0)
        self.assertEqual(self.qa.store.get_or_create_player(player['uin'])[0]['coin'],expected)
        self.now=row['home_at']
        returned=one(await self.until(conn,'War_SyncHistory',timeout=4),1)
        self.assertEqual(one(returned,8),self.wm.HOME)
        conn[1].close();await conn[1].wait_closed()
        again=await self.login(player['uin'])
        await self.send(again,'War_GetHistory');history=many(await self.until(again,'War_GetHistory'),1)
        self.assertEqual(len(history),1);self.assertEqual(one(history[0],8),self.wm.HOME)
        self.assertEqual(self.qa.store.get_or_create_player(player['uin'])[0]['coin'],expected)
        self.assertFalse(any('crashed' in x or 'timer failed' in x for x in self.records),self.records)


    async def test_two_idle_connections_receive_same_settlement_without_double_reward(self):
        player=self.qa.player(99100003,role_created=1,level=100,coin=12000)
        first=await self.login(player['uin']);second=await self.login(player['uin'])
        v=self.g.pb_varint_field;m=self.g.pb_bytes
        await self.ready_declaration(first)
        await self.send(first,'War_SetOut',v(1,2)+m(2,v(1,24)+v(2,18))+v(3,1))
        await self.until(first,'War_SetOut');await self.until(first,'War_SyncHistory')
        with closing(sqlite3.connect(self.qa.store.DB_PATH)) as db, db:
            db.row_factory=sqlite3.Row
            row=dict(db.execute('SELECT * FROM local_worldmap_missions WHERE uin=?',(player['uin'],)).fetchone())
        self.now=row['arrive_at']
        bodies=await asyncio.gather(self.until(first,'War_SyncHistory'),self.until(second,'War_SyncHistory'))
        self.assertTrue(all(one(one(b,1),8)==self.wm.RETURNING for b in bodies))
        self.assertEqual(self.qa.store.get_or_create_player(player['uin'])[0]['coin'],12000+(row['reward'] if row['won'] else 0))
        self.now=row['home_at']
        bodies=await asyncio.gather(self.until(first,'War_SyncHistory'),self.until(second,'War_SyncHistory'))
        self.assertTrue(all(one(one(b,1),8)==self.wm.HOME for b in bodies))
        self.assertFalse(any('crashed' in x or 'timer failed' in x for x in self.records),self.records)


    async def test_temporary_sqlite_timer_failure_retries_and_finishes(self):
        player=self.qa.player(99100004,role_created=1,level=100,coin=15000)
        conn=await self.login(player['uin']);v=self.g.pb_varint_field;m=self.g.pb_bytes
        await self.ready_declaration(conn)
        await self.send(conn,'War_SetOut',v(1,2)+m(2,v(1,24)+v(2,18))+v(3,1))
        await self.until(conn,'War_SetOut');await self.until(conn,'War_SyncHistory')
        with closing(sqlite3.connect(self.qa.store.DB_PATH)) as db, db:
            db.row_factory=sqlite3.Row
            row=dict(db.execute('SELECT * FROM local_worldmap_missions WHERE uin=?',(player['uin'],)).fetchone())
        self.now=row['arrive_at'];original=self.g._worldmap_adapter.advance;attempts=[]
        def fail_once(p):
            attempts.append(1)
            if len(attempts)==1:raise sqlite3.OperationalError('QA temporary timer failure')
            return original(p)
        with patch.object(self.g._worldmap_adapter,'advance',side_effect=fail_once):
            arrived=one(await self.until(conn,'War_SyncHistory',timeout=5),1)
        self.assertGreaterEqual(len(attempts),2);self.assertEqual(one(arrived,8),self.wm.RETURNING)
        self.now=row['home_at'];returned=one(await self.until(conn,'War_SyncHistory'),1)
        self.assertEqual(one(returned,8),self.wm.HOME)
        self.assertEqual(self.qa.store.get_or_create_player(player['uin'])[0]['coin'],15000+(row['reward'] if row['won'] else 0))
        self.assertFalse(any('handler ' in x and 'crashed' in x or 'worldmap timer failed' in x for x in self.records),self.records)


    async def test_unexpected_owned_route_error_returns_real_error_and_keeps_connection(self):
        player=self.qa.player(99100005,role_created=1,level=100)
        conn=await self.login(player['uin'])
        route='PVZ.Cmd.Cmd_WorldMap_GetInfo_CS'
        def broken(_data,_player):raise TypeError('QA deliberate route type error')
        with patch.dict(self.g.REQUEST_HANDLERS,{route:broken}):
            await self.send(conn,'WorldMap_GetInfo',self.g.pb_varint_field(5,1))
            error=await self.until(conn,'LogicError')
        self.assertEqual(one(error,1),3001)
        await self.send(conn,'City_Init');self.assertEqual(one(await self.until(conn,'City_Init'),2),1)
        self.assertFalse(any('sending generic' in record for record in self.records),self.records)


if __name__=='__main__':unittest.main(verbosity=2)
