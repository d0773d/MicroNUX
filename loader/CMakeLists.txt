cmake_minimum_required(VERSION 3.22)

option(MICRONUX_PROVISIONING_BUILD
       "Include the P4-hosted C6 provisioning component"
       OFF)

if(MICRONUX_PROVISIONING_BUILD)
    # Component dependency collection runs before project Kconfig is loaded.
    # Discover and force the optional wrapper only for this build profile; its
    # manifest then pulls in ESP-Hosted and provisioning without affecting
    # ordinary minimal builds.
    list(APPEND EXTRA_COMPONENT_DIRS
         "${CMAKE_CURRENT_LIST_DIR}/optional_components/micronux_provisioning")
    set(COMPONENTS main micronux_provisioning)
endif()

include($ENV{IDF_PATH}/tools/cmake/project.cmake)

if(NOT MICRONUX_PROVISIONING_BUILD)
    idf_build_set_property(MINIMAL_BUILD ON)
endif()

if(MICRONUX_PROVISIONING_BUILD)
    idf_build_set_property(
        DEPENDENCIES_LOCK
        "${CMAKE_CURRENT_LIST_DIR}/dependencies.provisioning.lock")
endif()

project(micronux_m3_loader)

if(CONFIG_MICRONUX_C6_PROVISIONING AND NOT MICRONUX_PROVISIONING_BUILD)
    message(FATAL_ERROR
        "CONFIG_MICRONUX_C6_PROVISIONING requires "
        "-DMICRONUX_PROVISIONING_BUILD=ON")
endif()

if(MICRONUX_PROVISIONING_BUILD AND NOT CONFIG_MICRONUX_C6_PROVISIONING)
    message(FATAL_ERROR
        "MICRONUX_PROVISIONING_BUILD requires the provisioning sdkconfig defaults")
endif()
