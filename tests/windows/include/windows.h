/* Minimal compile-time fixture for the device-selection test, not shipped runtime. */
#include <string.h>
#ifndef _WIN32
#include <strings.h>
#define _stricmp strcasecmp
#endif
#define TRUE 1
