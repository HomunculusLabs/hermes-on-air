"""The airing marker starts before synthesis; the aired receipt marks publication."""
import asyncio

from onair.stage import PendingLine, StageConfig, StageController


def test_airing_does_not_mean_voice_is_ready():
    async def run():
        started, finish = asyncio.Event(), asyncio.Event()
        class Voice:
            available = True
            async def synth(self, text):
                started.set()
                await finish.wait()
                return None
        controller = StageController(StageConfig(), tts=Voice())
        controller.set_mode('approval')
        epoch = controller.state.epoch
        controller._approval['line'] = PendingLine('line', epoch, 'turn', 'Hello.', 0, 'host')
        await controller.start()
        try:
            controller.approve('line', epoch)
            await asyncio.wait_for(started.wait(), 5)
            assert controller.snapshot()['airing'] == 'line'
            assert not any(r['kind'] == 'aired' for r in controller.receipts.tail(60))
            finish.set()
            async def aired():
                while not any(r['kind'] == 'aired' and r.get('line_id') == 'line' for r in controller.receipts.tail(60)):
                    await asyncio.sleep(.01)
            await asyncio.wait_for(aired(), 5)
        finally:
            finish.set()
            await controller.shutdown()
    asyncio.run(run())
