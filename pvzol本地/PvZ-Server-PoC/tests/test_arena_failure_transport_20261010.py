"""Failed arena requests must not send cardId=0 generic successful DTOs."""
from unittest.mock import patch
import unittest
import test_worldmap_tcp as tcp
from worldmap_test_support import one


class ArenaFailureTransportTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=tcp.WorldMapTCPTests.asyncSetUp
    asyncTearDown=tcp.WorldMapTCPTests.asyncTearDown
    connect=tcp.WorldMapTCPTests.connect
    send=tcp.WorldMapTCPTests.send
    until=tcp.WorldMapTCPTests.until
    login=tcp.WorldMapTCPTests.login
    async def test_arena_error_returns_logic_error_without_fake_report_or_disconnect(self):
        player=self.qa.player(99100112,role_created=1,level=100)
        conn=await self.login(player['uin'])
        def broken(_data,_player):
            raise TypeError('QA arena failure must not synthesize empty plants')
        for command in ('Arena_GetFormation','RankArena_GetBattleFormation',
                        'AllServerRankArena_GetOpponentFormation','Arena_Challenge',
                        'RankArena_Challenge','AllServerRankArena_Challenge',
                        'Battle_QueryReport','Battle_QueryRankReport'):
            with self.subTest(command=command),patch.dict(self.g.REQUEST_HANDLERS,
                    {'PVZ.Cmd.Cmd_'+command+'_CS':broken}):
                await self.send(conn,command)
                self.assertEqual(one(await self.until(conn,'LogicError'),1),3001)
        await self.send(conn,'City_Init')
        self.assertEqual(one(await self.until(conn,'City_Init'),2),1)
        self.assertFalse(any('sending generic' in record for record in self.records),self.records)
