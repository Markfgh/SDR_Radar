"""Radio contracts and mock sequence backend. Real backend lives in hardware.py."""

from typing import Protocol


class RadioBackend(Protocol):
    simulated: bool

    def tune(self, step, fc_hz): ...
    def capture(self, step, mode): ...
    def mute(self): ...
    def disable_tx(self): ...


class FakeRadioBackend:
    simulated = True

    def __init__(self, records, fault_mode=None):
        self.records = {(r.step, r.mode): r for r in records}
        self.events = []
        self.tx_enabled = False
        self.fault_mode = fault_mode
        self.epoch = 0

    def tune(self, step, fc_hz):
        self.events.append(("TUNE", step, fc_hz))
        self.fc_hz = fc_hz
        self.epoch += 1

    def capture(self, step, mode):
        if mode == self.fault_mode:
            raise TimeoutError("Injected acquisition timeout")
        self.tx_enabled = True
        self.events.append((mode, step, self.fc_hz))
        return self.records[step, mode]

    def mute(self):
        self.events.append(("MUTE",))
        self.tx_enabled = False

    def disable_tx(self):
        self.tx_enabled = False
        self.events.append(("TX_OFF",))
