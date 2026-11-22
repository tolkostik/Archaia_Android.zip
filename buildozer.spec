[app]
title = Archaia
package.name = archaia
package.domain = gr.archaia
source.dir = .
source.include_exts = py,jpg,json
source.exclude_dirs = .github, bin, .buildozer, tools
version = 1.1
requirements = python3,kivy==2.3.1,certifi,android
orientation = portrait
fullscreen = 0
android.permissions = INTERNET, ACCESS_FINE_LOCATION, ACCESS_COARSE_LOCATION
android.api = 34
android.minapi = 24
android.archs = arm64-v8a
android.accept_sdk_license = True
android.allow_backup = True

[buildozer]
log_level = 2
warn_on_root = 0
