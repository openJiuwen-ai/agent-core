"""Synthetic desktop for tool regression/CPU+IPC measurement. Never touches the real UI."""

from types import SimpleNamespace as NS

from PIL import Image, ImageDraw

from openjiuwen.harness.tools.desktop import worker
from tests.unit_tests.harness.tools.desktop.test_window_navigation import NativeRig


class PerformanceRig(NativeRig):
    """Real navigation/capture composition with injected OS metadata and patterned pixels."""

    def __init__(self, settings=None):
        super().__init__()
        self.actions, self.value, self.released = [], "", False
        self.after_action = None
        self.ax_status = "available"
        self.enabled = True
        self.label = "Apply"
        self.controls = [
            dict(
                role="AXTextArea", label="Entry", bounds=[15, 35, 85, 25], enabled=True, actionable=True, editable=True
            ),
            dict(role="AXButton", label="Apply", bounds=[15, 65, 30, 20], enabled=True, actionable=True),
            dict(role="AXButton", label="Next", bounds=[50, 65, 30, 20], enabled=True, actionable=True),
        ]
        self.d.settings = settings or self.d.settings.model_copy(update={"accessibility_enabled": True})
        self.d.accessibility = NS(clear=lambda: None, snapshot=self.snapshot)
        self.d._perform_action = self.perform
        self.d.release_inputs = lambda: setattr(self, "released", True)
        self.d.cursor_position = lambda _: None
        self.d._inputs = lambda: NS(check_stop=self.check_stop, pause=lambda _: self.check_stop())
        self._pattern = Image.new("RGB", (1000, 600), "#f4f5f7")
        canvas = ImageDraw.Draw(self._pattern)
        canvas.rectangle((0, 0, 1000, 40), fill="#27374d")
        canvas.rectangle((15, 55, 190, 580), fill="#e0e5eb")
        for index in range(24):
            y = 65 + index * 20
            canvas.text((210, y), f"Item {index + 1}: Review the draft and update the planned date.", fill="#17253a")
            if index % 3 == 0:
                canvas.rectangle((800, y, 950, y + 13), fill="#a0bbce")

    @staticmethod
    def check_stop():
        if worker.STOPPING.is_set():
            raise RuntimeError("desktop input cancelled")

    def snapshot(self, _app, observation, **_kwargs):
        items = [{**c, "id": f"{observation.generation}:{i}"} for i, c in enumerate(self.controls)]
        items[0]["value"] = self.value
        items[1].update(label=self.label, enabled=self.enabled)
        return items, self.ax_status

    def capture(self, native_id):
        self.captures.append(native_id)
        item = next(r for r in self.rows if r["kCGWindowNumber"] == native_id)
        _, _, width, height = self.d.bounds(item)
        image = self._pattern.resize((int(width * 2), int(height * 2))).convert("RGBA")
        ImageDraw.Draw(image).text((10, 10), f"{native_id}: {self.value}", fill="white")
        return image

    def perform(self, action):
        self.actions.append(action)
        if action.kind == "type":
            self.value += action.text
        elif action.kind == "shortcut":
            self.value += "|" + "+".join(action.keys)
        elif action.kind in {"click", "scroll"}:
            self.value += "|" + action.kind
        if self.after_action:
            self.after_action(action)


def serve():
    """Use the actual worker RPC protocol, lifecycle and new batch dispatch."""
    native_class = worker.NativeDesktop

    def create(settings):
        worker.NativeDesktop = native_class
        try:
            rig = PerformanceRig(settings)
            # Representative display-sized composition, fixed equally in both arms.
            rig.screen = rig.screen.model_copy(update={"width": 1280, "height": 800})
            for item, bounds in zip(rig.rows, [(10, 30, 500, 300), (700, 30, 500, 300), (20, 450, 500, 300)]):
                item["kCGWindowBounds"] = dict(zip(("X", "Y", "Width", "Height"), bounds))
        finally:
            worker.NativeDesktop = create
        rig.d._fixture = rig
        return rig.d

    worker.NativeDesktop = create
    worker.main()
