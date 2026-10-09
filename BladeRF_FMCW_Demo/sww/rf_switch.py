"""No physical switch API is invented. Manual requires independent position readback."""


class MockSwitchController:
    simulated = True

    def __init__(self, fault=False):
        self.position = "CAL"
        self.fault = fault

    def select(self, position):
        if position not in ("CAL", "RADAR"):
            raise ValueError("Unknown switch position")
        if self.fault:
            raise RuntimeError("Injected switch fault")
        self.position = position

    def read_position(self):
        return self.position


class ManualSwitchController:
    simulated = False

    def __init__(self, readback, confirm=input):
        if not callable(readback):
            raise ValueError("Independent position detection is required")
        self.readback = readback
        self.confirm = confirm

    def select(self, position):
        if position not in ("CAL", "RADAR"):
            raise ValueError("Unknown position")
        if (
            self.confirm(
                f"TX waveform muted. Set both RF switches to {position}; type {position}: "
            )
            != position
        ):
            raise RuntimeError("Manual switch confirmation rejected")
        if self.read_position() != position:
            raise RuntimeError("Switch readback mismatch")

    def read_position(self):
        return self.readback()
