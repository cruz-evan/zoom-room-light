# Keep boot lightweight. Runtime behavior starts in main.py after MicroPython
# mounts the filesystem and exposes USB serial.
#
# OTA trials are marked before main.py runs so a reset during trial can restore
# the previous app files before attempting to boot the candidate again.
try:
    from ota_client import prepare_trial_boot

    prepare_trial_boot()
except Exception:
    pass
