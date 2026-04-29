"""dmgbuild settings for MacroFlow.

This Script and Code created by:
Chad Littlepage
chad.littlepage@gmail.com
323.974.0444
"""

import os.path

APP_BUNDLE = "dist/MacroFlow.app"
APP_NAME = os.path.basename(APP_BUNDLE)

volume_name = "MacroFlow"
format = "UDZO"
filesystem = "HFS+"
size = None

window_rect = ((200, 120), (600, 400))
icon_size = 105
text_size = 13

background = "assets/dmg_background.jpg"

files = [APP_BUNDLE]
symlinks = {"Applications": "/Applications"}

icon_locations = {
    APP_NAME: (150, 200),
    "Applications": (450, 200),
}

default_view = "icon-view"
show_icon_preview = False
show_status_bar = False
show_tab_view = False
show_toolbar = False
show_pathbar = False
show_sidebar = False

text_color = (1.0, 1.0, 1.0)

hide_extension = [APP_NAME]
