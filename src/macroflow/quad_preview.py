"""QuadPreviewView — 16:9 quad-view monitor that highlights the active quadrant.

Ported from Chad's DaVinci Script Settings dialog and stripped down: no
title-style preview, no font/colour wiring — just the four quadrants with
labels and a single highlighted Q1/Q2/Q3/Q4 plus the optional track name.

This Script and Code created by:
Chad Littlepage
chad.littlepage@gmail.com
323.974.0444
"""

from __future__ import annotations

import objc
from AppKit import (
    NSBezierPath,
    NSColor,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSMakeRect,
    NSString,
    NSView,
)

# Cached colors / fonts. drawRect_ runs on every screen refresh + every
# quadrant change + every track selection — allocating these per call adds
# up. Module-level singletons cost nothing.
_BG_BLACK = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.08, 0.08, 0.08, 1.0)
_HIGHLIGHT_BLUE = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.0, 0.4, 0.85, 0.35)
_GRID_LINE = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.4, 0.4, 0.38, 1.0)
_BORDER = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.5, 0.5, 0.48, 1.0)
_DIM_LABEL = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.5, 0.5, 0.48, 1.0)
_BRIGHT_LABEL = NSColor.whiteColor()
_LABEL_FONT = NSFont.systemFontOfSize_(13)
_NAME_FONT = NSFont.boldSystemFontOfSize_(15)

# Quadrant labels are constant — cache the NSString instances so drawRect_
# doesn't allocate four fresh NSString objects per frame.
_Q_LABELS = {
    "Q1": NSString.stringWithString_("Q1"),
    "Q2": NSString.stringWithString_("Q2"),
    "Q3": NSString.stringWithString_("Q3"),
    "Q4": NSString.stringWithString_("Q4"),
}


class QuadPreviewView(NSView):
    """Draws a 16:9 quad-view monitor showing which quadrant is active.

    Also click-targetable: clicking anywhere in a quadrant calls a python
    callable installed via ``set_click_handler(fn)`` with the quadrant name
    ("Q1".."Q4"). The editor wires this to update the popup + macro state.
    """

    def init(self):
        self = objc.super(QuadPreviewView, self).init()
        if self is not None:
            self._init_defaults()
        return self

    def initWithFrame_(self, frame):  # NOQA: N802 (Cocoa accessor)
        # The editor allocates this view via initWithFrame_, which bypasses
        # init(). Without this override our instance attrs never get set
        # and the first drawRect_ raises AttributeError, which Cocoa
        # propagates as an ObjC exception that crashes the whole app.
        self = objc.super(QuadPreviewView, self).initWithFrame_(frame)
        if self is not None:
            self._init_defaults()
        return self

    @objc.python_method
    def _init_defaults(self) -> None:
        self._active_quad: str | None = None  # "Q1".."Q4" or None
        self._track_name: str = ""
        self._click_handler = None

    def set_active(self, quad_str: str | None, track_name: str = "") -> None:
        self._active_quad = quad_str
        self._track_name = track_name or ""
        self.setNeedsDisplay_(True)

    def set_click_handler(self, fn) -> None:
        self._click_handler = fn

    def mouseDown_(self, event):  # noqa: N802 (Cocoa accessor)
        if self._click_handler is None:
            return
        pt = self.convertPoint_fromView_(event.locationInWindow(), None)
        b = self.bounds()
        half_w = float(b.size.width) / 2.0
        half_h = float(b.size.height) / 2.0
        # NSView default coords: y=0 at bottom, y up.
        if pt.y >= half_h:
            quad = "Q1" if pt.x < half_w else "Q2"
        else:
            quad = "Q3" if pt.x < half_w else "Q4"
        try:
            self._click_handler(quad)
        except Exception:
            pass

    def drawRect_(self, rect):  # NOQA: N802 (Cocoa accessor)
        # Hard guard: Cocoa propagates Python exceptions raised in
        # drawRect_ back through PyObjCErr_ToObjCWithGILState, which
        # crashes the entire app. Wrap the whole body so a draw-time
        # error logs and skips a frame instead of taking the app down.
        try:
            self._draw_quad_preview(rect)
        except Exception as e:
            print(f"[quad_preview] drawRect_ error: {e!r}")

    @objc.python_method
    def _draw_quad_preview(self, rect):
        # Editor allocates this view via initWithFrame_, which bypasses our
        # custom init() — so the instance attrs may not exist on first draw.
        # Read them defensively rather than raising AttributeError into
        # Cocoa's draw chain (which crashes the app on macOS 15).
        active_quad = getattr(self, "_active_quad", None)
        track_name = getattr(self, "_track_name", "") or ""

        frame = self.bounds()
        fw, fh = float(frame.size.width), float(frame.size.height)

        # Fit a 16:9 monitor centered in the view.
        aspect = 16.0 / 9.0
        if fw / fh > aspect:
            draw_h = fh - 4
            draw_w = draw_h * aspect
        else:
            draw_w = fw - 4
            draw_h = draw_w / aspect
        ox = (fw - draw_w) / 2.0
        oy = (fh - draw_h) / 2.0

        # Dark "screen" background.
        _BG_BLACK.set()
        NSBezierPath.bezierPathWithRect_(NSMakeRect(ox, oy, draw_w, draw_h)).fill()

        half_w = draw_w / 2.0
        half_h = draw_h / 2.0

        # Cocoa Y is bottom-up: Q1 = top-left, Q2 = top-right, Q3 = bottom-left, Q4 = bottom-right.
        quads = {
            "Q1": NSMakeRect(ox, oy + half_h, half_w, half_h),
            "Q2": NSMakeRect(ox + half_w, oy + half_h, half_w, half_h),
            "Q3": NSMakeRect(ox, oy, half_w, half_h),
            "Q4": NSMakeRect(ox + half_w, oy, half_w, half_h),
        }

        if active_quad in quads:
            _HIGHLIGHT_BLUE.set()
            NSBezierPath.bezierPathWithRect_(quads[active_quad]).fill()

        _GRID_LINE.set()
        vline = NSBezierPath.bezierPath()
        vline.moveToPoint_((ox + half_w, oy))
        vline.lineToPoint_((ox + half_w, oy + draw_h))
        vline.setLineWidth_(1.0)
        vline.stroke()
        hline = NSBezierPath.bezierPath()
        hline.moveToPoint_((ox, oy + half_h))
        hline.lineToPoint_((ox + draw_w, oy + half_h))
        hline.setLineWidth_(1.0)
        hline.stroke()

        _BORDER.set()
        border = NSBezierPath.bezierPathWithRect_(NSMakeRect(ox, oy, draw_w, draw_h))
        border.setLineWidth_(1.5)
        border.stroke()

        for q_name, q_rect in quads.items():
            is_active = (q_name == active_quad)
            qx = float(q_rect.origin.x)
            qy = float(q_rect.origin.y)
            qw = float(q_rect.size.width)
            qh = float(q_rect.size.height)

            attrs = {
                NSFontAttributeName: _LABEL_FONT,
                NSForegroundColorAttributeName: (
                    _BRIGHT_LABEL if is_active else _DIM_LABEL
                ),
            }
            label_str = _Q_LABELS.get(q_name) or NSString.stringWithString_(q_name)
            label_size = label_str.sizeWithAttributes_(attrs)
            lx = qx + (qw - float(label_size.width)) / 2.0
            ly = qy + qh / 2.0 + 6
            label_str.drawAtPoint_withAttributes_((lx, ly), attrs)

            if is_active and track_name:
                name_attrs = {
                    NSFontAttributeName: _NAME_FONT,
                    NSForegroundColorAttributeName: _BRIGHT_LABEL,
                }
                name_str = NSString.stringWithString_(track_name)
                name_size = name_str.sizeWithAttributes_(name_attrs)
                nx = qx + (qw - float(name_size.width)) / 2.0
                ny = qy + (ly - qy) / 2.0 - float(name_size.height) / 2.0
                name_str.drawAtPoint_withAttributes_((nx, ny), name_attrs)
