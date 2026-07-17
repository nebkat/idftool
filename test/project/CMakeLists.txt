cmake_minimum_required(VERSION 3.16)

# The fixture build script sets IDFTOOL_TEST_VER to stamp the app-descriptor version, so we can
# produce two otherwise-identical builds (v1 / v2) to OTA between. Falls back to a default.
if(DEFINED ENV{IDFTOOL_TEST_VER})
    set(PROJECT_VER "$ENV{IDFTOOL_TEST_VER}")
else()
    set(PROJECT_VER "0.0.0")
endif()

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(idftool_test)
