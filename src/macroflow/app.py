"""Main Cocoa app: a clickable grid of macro buttons.

Each cell fires a Macro (Videohub preset + Resolve track state, in parallel).
Right-click or double-click a cell to open the macro editor.

This Script and Code created by:
Chad Littlepage
chad.littlepage@gmail.com
323.974.0444
"""

from __future__ import annotations

import sys

from AppKit import (
    NSApp,
    NSApplication,
    NSApplicationActivationPolicyRegular,
    NSBackingStoreBuffered,
    NSButton,
    NSColor,
    NSEventTypeRightMouseDown,
    NSMakeRect,
    NSObject,
    NSTextField,
    NSView,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
)
from objc import super  # type: ignore

from macroflow.macro import Macro, MacroStore
from macroflow.macro_editor import MacroEditorWindow

GRID_PADDING = 16
CELL_PADDING = 8
LCD_HEIGHT = 36

PRIMARY_COLOR = (0x4a / 255.0, 0x55 / 255.0, 0x6c / 255.0, 1.0)


class _MacroCellButton(NSButton):
    """One cell of the macro grid. Click = fire. Right-click = edit."""

    def initWithFrame_row_col_controller_(self, frame, row, col, controller):  # NOQA: N802
        self = super().initWithFrame_(frame)
        if self is None:
            return None
        self._row = row
        self._col = col
        self._controller = controller
        self.setBezelStyle_(1)  # rounded
        self.setButtonType_(0)  # NSMomentaryPushInButton
        self.setTarget_(self)
        self.setAction_("fire:")
        return self

    def fire_(self, sender) -> None:  # NOQA: N802
        self._controller.fireCell_col_(self._row, self._col)

    def rightMouseDown_(self, event) -> None:  # NOQA: N802
        self._controller.editCell_col_(self._row, self._col)

    def mouseDown_(self, event) -> None:  # NOQA: N802
        if event.clickCount() >= 2:
            self._controller.editCell_col_(self._row, self._col)
            return
        super().mouseDown_(event)


class AppController(NSObject):
    """Owns the window, grid, and macro store."""

    def init(self):
        self = super().init()
        if self is None:
            return None
        self._store = MacroStore()
        self._cell_buttons: dict[tuple[int, int], _MacroCellButton] = {}
        self._build_window()
        return self

    # -- Window ---------------------------------------------------------------

    def _build_window(self) -> None:
        rows = self._store.grid.rows
        cols = self._store.grid.cols
        cell_w, cell_h = 140, 100
        win_w = GRID_PADDING * 2 + cols * cell_w + (cols - 1) * CELL_PADDING
        win_h = GRID_PADDING * 2 + rows * cell_h + (rows - 1) * CELL_PADDING + LCD_HEIGHT

        style = (NSWindowStyleMaskTitled
                 | NSWindowStyleMaskClosable
                 | NSWindowStyleMaskMiniaturizable
                 | NSWindowStyleMaskResizable)
        self._window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(200, 200, win_w, win_h),
            style, NSBackingStoreBuffered, False,
        )
        self._window.setTitle_("MacroFlow")
        self._window.setReleasedWhenClosed_(False)

        content = self._window.contentView()
        if content is None:
            return

        # LCD strip (last fired macro)
        lcd = NSTextField.alloc().initWithFrame_(
            NSMakeRect(GRID_PADDING, win_h - LCD_HEIGHT - 4,
                       win_w - GRID_PADDING * 2, LCD_HEIGHT - 6),
        )
        lcd.setBezeled_(True)
        lcd.setEditable_(False)
        lcd.setSelectable_(False)
        lcd.setStringValue_("MacroFlow ready — click a cell to fire, right-click to edit")
        lcd.setBackgroundColor_(NSColor.colorWithCalibratedRed_green_blue_alpha_(*PRIMARY_COLOR))
        lcd.setTextColor_(NSColor.whiteColor())
        content.addSubview_(lcd)
        self._lcd = lcd

        # Grid
        grid_top = win_h - LCD_HEIGHT - 4
        for r in range(rows):
            for c in range(cols):
                x = GRID_PADDING + c * (cell_w + CELL_PADDING)
                y = grid_top - (r + 1) * cell_h - r * CELL_PADDING - 4
                btn = _MacroCellButton.alloc().initWithFrame_row_col_controller_(
                    NSMakeRect(x, y, cell_w, cell_h), r, c, self,
                )
                content.addSubview_(btn)
                self._cell_buttons[(r, c)] = btn
        self._refresh_cell_titles()

        self._window.makeKeyAndOrderFront_(None)

    def _refresh_cell_titles(self) -> None:
        for (r, c), btn in self._cell_buttons.items():
            macro = self._store.grid.get(r, c)
            btn.setTitle_(self._title_for(macro, r, c))

    @staticmethod
    def _title_for(macro: Macro | None, r: int, c: int) -> str:
        if macro is None or (not macro.label
                              and not macro.videohub.is_set()
                              and not macro.resolve.is_set()):
            return f"R{r+1}C{c+1}\n(empty)"
        lines = [macro.label or f"R{r+1}C{c+1}"]
        if macro.videohub.is_set():
            lines.append(f"VH: {macro.videohub.preset_name}")
        if macro.resolve.is_set():
            on = sum(1 for v in macro.resolve.tracks.values() if v)
            off = sum(1 for v in macro.resolve.tracks.values() if not v)
            lines.append(f"Resolve: {on}+/{off}-")
        return "\n".join(lines)

    # -- Cell interactions ----------------------------------------------------

    def fireCell_col_(self, row: int, col: int) -> None:  # NOQA: N802
        macro = self._store.grid.get(row, col)
        if macro is None:
            self._lcd.setStringValue_(f"R{row+1}C{col+1} is empty")
            return
        self._lcd.setStringValue_(f"Firing: {macro.label or macro.id}")
        results = macro.fire()
        bits = [f"{k}={'OK' if v else 'FAIL'}" for k, v in results.items()] or ["(no actions)"]
        self._lcd.setStringValue_(f"{macro.label or macro.id} — " + " ".join(bits))

    def editCell_col_(self, row: int, col: int) -> None:  # NOQA: N802
        macro = self._store.grid.get(row, col)
        if macro is None:
            macro = Macro(id=self._store.grid.cell_id(row, col))
        editor = MacroEditorWindow.alloc().initWithMacro_(macro)
        editor.setSaveCallback_(lambda m: self._on_macro_saved(row, col, m))
        editor.makeKeyAndOrderFront_(None)
        self._editor = editor  # keep a reference so it isn't released

    def _on_macro_saved(self, row: int, col: int, macro: Macro) -> None:
        empty = (not macro.label
                 and not macro.videohub.is_set()
                 and not macro.resolve.is_set())
        if empty:
            self._store.grid.clear(row, col)
        else:
            self._store.grid.set(row, col, macro)
        self._store.save()
        self._refresh_cell_titles()


def main() -> int:
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    controller = AppController.alloc().init()  # NOQA: F841 (kept alive by NSApp)
    NSApp.activate()
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
