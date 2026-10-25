# Third-party notices

`spc5tool/webui/assets/tigard-render.png` is the unchanged Tigard v1.0 board
rendering by Franklin Harding, copyright 2020, from
[tigard-tools/tigard](https://github.com/tigard-tools/tigard/blob/master/tigard-render.png).
It is distributed under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/).
The full upstream license is included at
[TIGARD-LICENSE.txt](spc5tool/webui/assets/TIGARD-LICENSE.txt).
The UI scales the rendering and overlays connector highlights; the image itself
is unchanged. Wiring screenshots retain the same attribution.

Original code, connector diagrams, and prose are MIT licensed. Python and npm
dependencies are installed separately under their own licenses; their source
is not vendored here. Tigard, NXP, STMicroelectronics, P&E Micro, and
Winbond names identify products or compatibility targets. This project is
independent and is not endorsed by those companies or projects.

`libusb-package` (Apache-2.0) installs libusb (LGPL-2.1-or-later) separately as a
runtime dependency. The separate Windows USB helper links libwdi
(LGPL-3.0-or-later) and embeds upstream libusb-win32/libusbK driver components.
It is distributed with libwdi source/license notices and a build recipe. See
[Windows USB setup](docs/WINDOWS_USB.md) for exact versions and corresponding
source links. These binaries are not included in the Python wheel or Git tree.

No proprietary flasher executable, programming algorithm binary, PCP file,
customer firmware, adapter serial number, or hardware dump is distributed.
