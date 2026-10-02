# How to install libgphoto2 on Windows

## Introduction

We will install `gphoto2` which installs the required library and enables us to test the installation. `gphoto2` is a command line utility using `libgphoto2` to to work with the camera.

It talks to USB connected camera using `libusb` compatible driver. For this we will install `WinUSB` driver using [Zadig](https://zadig.akeo.ie).

Note that we will install UCRT64 version (and not MINGW64).


## Installation

### MSYS2 gphoto2

1. Download latest version of [MSYS2](https://www.msys2.org/).
2. Run the installation, keep the defaults (next, next, install, next, run now, finish).
3. Go to the terminal which opened if you left checkbox "Run now" (otherwise just open newly installed "MSYS2 UCRT64").
4. Run (and accept all):

```bash
pacman -S mingw-w64-ucrt-x86_64-gphoto2
```

### WinUSB driver (using Zadig)

1. Install [Zadig](https://zadig.akeo.ie)
2. Connect your camera (camera on).
3. Run `Zadig`, select your camera from the list and driver `WinUSB and click install. This may take some time

### Optional checks

```bash
# print version
gphoto2 --version

# show connected camera(s)
gphoto2 --auto-detect

# basic image capture
gphoto2 --capture-image-and-download

gphoto2  --list-config
```
