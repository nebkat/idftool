// Minimal ESP-IDF app used only to generate idftool test fixtures.
// It does nothing useful on purpose — the tests never check what it does, only that idftool can
// flash/read/inspect it. The app-descriptor version is stamped via PROJECT_VER (see CMakeLists.txt)
// so two builds (v1 / v2) are distinguishable for OTA tests.

#include <stdio.h>
#include "esp_app_desc.h"

void app_main(void)
{
    const esp_app_desc_t *desc = esp_app_get_description();
    printf("idftool-test app, version %s\n", desc->version);
}
