struct wdi_device_info {
    struct wdi_device_info *next;
    unsigned short vid, pid;
    int is_composite;
    char *desc, *driver;
};
struct wdi_options_create_list { int list_all, list_hubs, trim_whitespaces; };
struct wdi_options_prepare_driver { int driver_type; char *vendor_name; };
struct wdi_options_install_driver { unsigned long pending_install_timeout; };
#define WDI_LIBUSB0 1
#define WDI_SUCCESS 0
int wdi_is_driver_supported(int type, void *info);
int wdi_create_list(struct wdi_device_info **list, struct wdi_options_create_list *options);
void wdi_destroy_list(struct wdi_device_info *list);
int wdi_prepare_driver(struct wdi_device_info *dev, const char *dir, const char *inf, struct wdi_options_prepare_driver *options);
int wdi_install_driver(struct wdi_device_info *dev, const char *dir, const char *inf, struct wdi_options_install_driver *options);
const char *wdi_strerror(int code);
