"""Test-only flash doubles. The installed application never imports this module."""
from contextlib import contextmanager
from pathlib import Path

from spc5tool.chips import get
from spc5tool.files import load_state, save_state
from spc5tool.flash import Flash
from spc5tool.spi import PARTS, SpiNor
from tests.simulator import SimMemory, SimSpiPort


@contextmanager
def flash_model(target, path, progress=None):
    path = Path(path)
    if target in PARTS:
        memory = SimSpiPort(PARTS[target])
        regions = {0: memory.data}
        flash = SpiNor(memory, memory.part)
    else:
        memory = SimMemory(get(target))
        regions = memory.regions
        flash = Flash(memory, memory.chip, progress=progress)
    load_state(path, target, regions)
    try:
        yield flash
    finally:
        path.parent.mkdir(parents=True, exist_ok=True)
        save_state(path, target, regions)


def workspace_backend(workspace, plan, progress):
    return flash_model(plan['target'], workspace.directory / 'states' / (plan['target'] + '.zip'), progress)
