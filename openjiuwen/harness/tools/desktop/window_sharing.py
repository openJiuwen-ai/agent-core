"""Host-selected native window lifetimes. No application-wide capture grant."""

from __future__ import annotations


class SharedWindows:
    """Bind initial selectors once; an observed disappearance revokes a root grant."""

    def __init__(self):
        self.roots = {}
        self.pending_launches = set()

    def update(self, targets, instances, surfaces):
        """Resolve host grants and expire windows that disappear."""
        visible = {(s.pid, s.native_id) for s in surfaces}
        for name, (pid, _) in instances.items():
            if name in self.roots:
                self.roots[name].intersection_update(visible)
                continue
            spec = targets[name]
            candidates = [s for s in surfaces if s.pid == pid and s.layer == 0]
            if spec.window_ids:
                selected = {(pid, wid) for wid in spec.window_ids}
                if not selected <= visible:
                    raise PermissionError("a shared window is unavailable; select it again in the host")
            elif spec.window_title:
                matches = [s for s in candidates if s.title == spec.window_title]
                if len(matches) != 1:
                    raise PermissionError("shared window title must resolve to exactly one native window")
                selected = {(pid, matches[0].native_id)}
            elif name in self.pending_launches:
                if not candidates:
                    continue
                if len(candidates) != 1:
                    raise PermissionError("launch produced multiple windows; share the intended window explicitly")
                selected = {(pid, candidates[0].native_id)}
                self.pending_launches.remove(name)
            else:
                raise PermissionError(
                    "share explicit window_ids or one exact window_title; app-wide access is disabled"
                )
            self.roots[name] = selected
        return set().union(*self.roots.values()) if self.roots else set()
