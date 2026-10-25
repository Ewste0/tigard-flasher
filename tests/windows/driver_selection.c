/* Execute the real helper control flow with a fake libwdi. Never install a driver. */
#include <assert.h>
#define main helper_main
#include "../../tools/windows/tigard_usb_driver.c"
#undef main
static struct wdi_device_info *devices;
static int prepared, installed, prepare_error;
int wdi_is_driver_supported(int type, void *info) { return type == WDI_LIBUSB0; }
int wdi_create_list(struct wdi_device_info **list, struct wdi_options_create_list *options) {
    assert(options->list_all && options->list_hubs); *list = devices; return 0;
}
void wdi_destroy_list(struct wdi_device_info *list) { (void)list; }
int wdi_prepare_driver(struct wdi_device_info *dev, const char *dir, const char *inf, struct wdi_options_prepare_driver *options) {
    assert(dev->vid == 0x0403 && dev->pid == 0x6010 && !dev->is_composite);
    assert(options->driver_type == WDI_LIBUSB0); prepared++; return prepare_error;
}
int wdi_install_driver(struct wdi_device_info *dev, const char *dir, const char *inf, struct wdi_options_install_driver *options) {
    assert(options->pending_install_timeout == 120000); installed++; return 0;
}
const char *wdi_strerror(int code) { return code ? "fixture failure" : "fixture success"; }
int main(void) {
    char *install[] = {"helper", "--install", "fixture-output"};
    char *check[] = {"helper", "--check"};
    struct wdi_device_info tigard = {NULL, 0x0403, 0x6010, 0, "Tigard V1.2", "FTDIBUS"};
    struct wdi_device_info other = {NULL, 0x0403, 0x6010, 0, "Other FTDI", "FTDIBUS"};
    assert(helper_main(3, install) == 6);
    devices = &other; assert(helper_main(3, install) == 6);
    devices = &tigard; tigard.is_composite = 1; assert(helper_main(3, install) == 6);
    tigard.is_composite = 0; tigard.vid = 0x1234; assert(helper_main(3, install) == 6);
    tigard.vid = 0x0403; tigard.next = &other; assert(helper_main(3, install) == 6);
    other.desc = "Tigard V1.1"; assert(helper_main(3, install) == 6);
    assert(prepared == 0 && installed == 0);
    tigard.next = NULL; assert(helper_main(2, check) == 10);
    tigard.driver = "libusb0"; assert(helper_main(2, check) == 0);
    assert(prepared == 0 && installed == 0);
    prepare_error = -1; assert(helper_main(3, install) == 7); assert(installed == 0);
    prepare_error = 0; assert(helper_main(3, install) == 0); assert(installed == 1);
    puts("Driver selection tests passed; no OS driver calls were made.");
    return 0;
}
