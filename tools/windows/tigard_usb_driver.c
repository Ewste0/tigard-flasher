/* SPDX-License-Identifier: MIT
 * Built against libwdi (LGPL-3.0-or-later). No JTAG or target-memory access.
 */
#include <stdio.h>
#include <string.h>
#include <windows.h>
#include "libwdi.h"

static int is_tigard(const struct wdi_device_info *dev) {
    return dev->desc && (strstr(dev->desc, "Tigard") || strstr(dev->desc, "TIGARD") || strstr(dev->desc, "tigard"));
}

int main(int argc, char **argv) {
    struct wdi_device_info *list = NULL, *dev, *selected = NULL;
    struct wdi_options_create_list listing = {0};
    struct wdi_options_prepare_driver prepare = {0};
    struct wdi_options_install_driver install = {0};
    int count = 0, other = 0, result;
    if (argc == 2 && !strcmp(argv[1], "--help")) {
        puts("Tigard USB driver: --check | --install <extraction-directory> | --extract <directory>");
        return 0;
    }
    if (!((argc == 2 && !strcmp(argv[1], "--check")) ||
          (argc == 3 && (!strcmp(argv[1], "--install") || !strcmp(argv[1], "--extract"))))) return 2;
    prepare.driver_type = WDI_LIBUSB0;
    prepare.vendor_name = "Tigard";
    if (!wdi_is_driver_supported(WDI_LIBUSB0, NULL)) return 3;
    if (!strcmp(argv[1], "--extract")) {
        /* Build validation only: extract files without installing any driver. */
        struct wdi_device_info fixture = {0};
        fixture.vid = 0x0403; fixture.pid = 0x6010; fixture.desc = "Tigard";
        result = wdi_prepare_driver(&fixture, argv[2], "tigard.inf", &prepare);
        puts(wdi_strerror(result));
        return result == WDI_SUCCESS ? 0 : 4;
    }
    listing.list_all = TRUE;
    listing.list_hubs = TRUE;
    listing.trim_whitespaces = TRUE;
    result = wdi_create_list(&list, &listing);
    if (result != WDI_SUCCESS) { puts(wdi_strerror(result)); return 5; }
    for (dev = list; dev; dev = dev->next) {
        /* libwdi is_composite means an individual MI_xx interface here. */
        if (dev->vid != 0x0403 || dev->pid != 0x6010 || dev->is_composite) continue;
        if (is_tigard(dev)) { selected = dev; count++; } else other++;
    }
    if (count != 1 || other) {
        puts("Connect exactly one Tigard and disconnect other FT2232H adapters before USB driver setup.");
        wdi_destroy_list(list); return 6;
    }
    printf("Selected: %s (%04X:%04X), driver: %s\n", selected->desc, selected->vid,
           selected->pid, selected->driver ? selected->driver : "none");
    if (!strcmp(argv[1], "--check")) {
        result = selected->driver && !_stricmp(selected->driver, "libusb0") ? 0 : 10;
        wdi_destroy_list(list); return result;
    }
    puts("Installing libusb-win32 on the Tigard composite parent. Its COM ports may disappear.");
    result = wdi_prepare_driver(selected, argv[2], "tigard.inf", &prepare);
    if (result == WDI_SUCCESS) {
        install.pending_install_timeout = 120000;
        result = wdi_install_driver(selected, argv[2], "tigard.inf", &install);
    }
    puts(wdi_strerror(result));
    wdi_destroy_list(list);
    return result == WDI_SUCCESS ? 0 : 7;
}
