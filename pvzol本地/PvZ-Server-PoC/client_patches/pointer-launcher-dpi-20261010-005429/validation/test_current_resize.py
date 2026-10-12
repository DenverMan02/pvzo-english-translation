from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace

from actual_vm import VM, Rectangle, MouseEvent, mock_starling

SWF = Path(os.environ.get("PVZ_RESIZE_SWF", Path(__file__).resolve().parents[1] / "tower/Game_p.tower.swf"))
STARLING = "starling.core::Starling"
CONTEXT = "com.qq.modules.main::MainContext"
RESULTS = []


class StarlingObject(SimpleNamespace):
    def render(self):
        # This geometry fixture substitutes only the two actual render viewport
        # calls. Full initialize/render execution has a separate GPU-boundary
        # timing fixture in startup-whiteflash/test_startup_present.py.
        self.vm.execute(STARLING, "updateViewPort", self, [False])
        self.vm.execute(STARLING, "updateNativeOverlay", self)

    @property
    def viewPort(self):
        return self.vm.execute(STARLING, "viewPort", self, kind=2)

    @viewPort.setter
    def viewPort(self, value):
        self.vm.execute(STARLING, "viewPort", self, [value], kind=3)


class Event(SimpleNamespace):
    RESIZE = "resize"

    def __init__(self, event_type):
        super().__init__(type=event_type)


class ResizeBytecodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vm = VM(SWF)

    def scene(self, width, height):
        original, backbuffers, touches = mock_starling(width, height)
        starling = StarlingObject(**vars(original), vm=self.vm)
        system = SimpleNamespace(x=0, y=0, scaleX=1, scaleY=1)
        context = SimpleNamespace(contextView=SimpleNamespace(stage=starling.mNativeStage))
        self.vm.globals["com.qq::GameGloble"] = SimpleNamespace(starling=starling)
        self.vm.globals["asgui.managers::SystemManager"] = SimpleNamespace(instance=system)
        return context, starling, system, backbuffers, touches

    def resize(self, context, starling):
        self.vm.execute(CONTEXT, "onWindowResize", context, [None])
        self.vm.execute(STARLING, "updateViewPort", starling, [False])
        self.vm.execute(STARLING, "updateNativeOverlay", starling)

    def assert_geometry_and_hit_test(self, width, height):
        context, starling, system, backbuffers, touches = self.scene(width, height)
        self.resize(context, starling)
        rect = starling.mViewPort
        self.assertGreaterEqual(rect.x, -1e-9)
        self.assertGreaterEqual(rect.y, -1e-9)
        self.assertLessEqual(rect.x + rect.width, width + 1e-9)
        self.assertLessEqual(rect.y + rect.height, height + 1e-9)
        self.assertAlmostEqual(rect.width / rect.height, 1.6)
        self.assertAlmostEqual(rect.x, (width - rect.width) / 2)
        self.assertAlmostEqual(rect.y, (height - rect.height) / 2)
        self.assertTrue(abs(rect.width-width) < 1e-9 or abs(rect.height-height) < 1e-9)
        self.assertEqual((starling.mStage.stageWidth, starling.mStage.stageHeight), (1440, 900))
        self.assertAlmostEqual(system.x, rect.x)
        self.assertAlmostEqual(system.y, rect.y)
        self.assertAlmostEqual(system.scaleX, rect.width / 1440)
        self.assertAlmostEqual(system.scaleY, rect.height / 900)
        self.assertAlmostEqual(starling.mNativeOverlay.x, system.x)
        self.assertAlmostEqual(starling.mNativeOverlay.y, system.y)
        self.assertAlmostEqual(starling.mNativeOverlay.scaleX, system.scaleX)
        self.assertAlmostEqual(starling.mNativeOverlay.scaleY, system.scaleY)
        self.assertAlmostEqual(starling.mStage3D.x, rect.x)
        self.assertAlmostEqual(starling.mStage3D.y, rect.y)
        self.assertEqual(len(backbuffers), 1)
        self.assertAlmostEqual(backbuffers[0][0], rect.width)
        self.assertAlmostEqual(backbuffers[0][1], rect.height)
        self.assertEqual(backbuffers[0][2:], (0, False, False))
        self.assertGreaterEqual(int(backbuffers[0][0]), 32)
        self.assertGreaterEqual(int(backbuffers[0][1]), 32)
        for logical_x, logical_y in [(0, 0), (1440, 900), (72, 100), (719, 449), (1400, 880)]:
            event = MouseEvent(type=MouseEvent.MOUSE_DOWN,
                               stageX=system.x + logical_x * system.scaleX,
                               stageY=system.y + logical_y * system.scaleY)
            self.vm.execute(STARLING, "onTouch", starling, [event])
            self.assertEqual(touches[-1][:2], (0, "began"))
            self.assertAlmostEqual(touches[-1][2], logical_x)
            self.assertAlmostEqual(touches[-1][3], logical_y)
        RESULTS.append({"window": [width, height], "viewport": vars(rect),
                        "scale": [system.scaleX, system.scaleY],
                        "backbuffer": backbuffers[0][:2], "hit_points": 5})

    def test_window_sizes(self):
        for size in [(1440,900),(1920,1080),(3840,2018),(3840,2088),(3840,2160),
                     (2560,1440),(1920,1200),(3440,1440),(1280,1024),(1600,900),
                     (1536,864),(1280,800),(1024,768),(800,600),(1200,800),(700,500),
                     (64,40),(80,40),(52,33)]:
            with self.subTest(window=size):
                self.assert_geometry_and_hit_test(*size)

    def test_repeated_size_does_not_reallocate_gpu_buffer(self):
        context, starling, system, backbuffers, touches = self.scene(1920,1080)
        self.resize(context, starling)
        self.resize(context, starling)
        self.assertEqual(len(backbuffers), 1)

    def test_maximize_restore_and_minimize_sequence(self):
        context, starling, system, backbuffers, touches = self.scene(1440,900)
        self.resize(context, starling)
        for width, height in [(3840,2018),(1440,900),(0,0),(50,20),(1,1),(1440,900)]:
            old = dict(vars(starling.mViewPort))
            old_system = dict(vars(system))
            old_count = len(backbuffers)
            starling.mNativeStage.stageWidth = width
            starling.mNativeStage.stageHeight = height
            self.resize(context, starling)
            if (width,height) in [(0,0),(50,20),(1,1)]:
                self.assertEqual(vars(starling.mViewPort), old)
                self.assertEqual(vars(system), old_system)
                self.assertEqual(len(backbuffers), old_count)
        self.assertEqual(vars(starling.mViewPort), {"x":0,"y":0,"width":1440,"height":900})
        self.assertEqual(vars(system), {"x":0,"y":0,"scaleX":1,"scaleY":1})
        self.assertEqual(len(backbuffers), 3)

    def test_application_layout_stays_in_design_coordinates(self):
        events, timeouts = [], []
        self.vm.globals["flash.events::Event"] = Event
        self.vm.globals["flash.utils::clearTimeout"] = lambda value: timeouts.append(value)
        container = SimpleNamespace(checkSizeTimeOut=13, width=1, height=1,
                                    dispatchEvent=lambda event: events.append(event.type))
        self.vm.execute("asgui.core::LayoutContainer", "CheckSize", container)
        self.assertEqual((container.width,container.height), (1440,900))
        self.assertEqual(events, [Event.RESIZE])
        self.assertEqual(timeouts, [13])


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ResizeBytecodeTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    out = Path(__file__).with_name("resize_geometry_summary.json")
    out.write_text(json.dumps({"swf":str(SWF), "test_count":result.testsRun,
                               "passed":result.wasSuccessful(), "sizes":RESULTS},
                              ensure_ascii=False,indent=2),encoding="utf-8")
    raise SystemExit(0 if result.wasSuccessful() else 1)
