# udev rules

Copied unchanged from the pyOCD repository (`udev/`, revision d1974ff,
2026-07-21), Apache-2.0: https://github.com/pyocd/pyOCD/tree/main/udev

They grant your user access to ST-Link, CMSIS-DAP and a few other debug
probes. J-Link is not among them: SEGGER's J-Link software pack, which the
probe needs on Linux anyway, installs its own rule.

```bash
sudo cp udev/*.rules /etc/udev/rules.d/
sudo udevadm control --reload && sudo udevadm trigger
```

Unplug and replug the probe afterwards.
