cmake_minimum_required(VERSION 3.21)
project(chiaki-taskui LANGUAGES CXX)

set(CMAKE_AUTOMOC ON)

find_package(Qt6 REQUIRED COMPONENTS Core Gui Qml Quick Network TaskTree)
qt_standard_project_setup()

# Consume the Soloway.QtTaskTree module, vendored as a git submodule at
# taskui/tasktree. Override with -DTASKTREE_DIR=<path> if needed. Skip its tests.
set(TASKTREE_DIR
    "${CMAKE_CURRENT_SOURCE_DIR}/tasktree"
    CACHE PATH "Path to the Soloway.QtTaskTree module source")
set(BUILD_TESTING OFF CACHE BOOL "" FORCE)
add_subdirectory(${TASKTREE_DIR} ${CMAKE_BINARY_DIR}/soloway-qt-tasktree)

# UI module on a static library so the app and the QML test harness share it
# (a module on the executable is unreachable from a sibling test binary).
qt_add_library(chiaki-taskui-ui STATIC
    ChiakiTaskBridge.cpp
    ChiakiTaskBridge.h
    ChiakiProcess.cpp
    ChiakiProcess.h
)
qt_add_qml_module(chiaki-taskui-ui
    URI ChiakiTaskUi
    VERSION 1.0
    # Embed at :/qt/qml so the engine's default import path resolves the module
    # in deployed binaries too (build trees also work via the appdir path).
    RESOURCE_PREFIX /qt/qml
    QML_FILES
        qml/Main.qml
        qml/StepEditor.qml
)
target_include_directories(chiaki-taskui-ui PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
target_link_libraries(chiaki-taskui-ui
    PUBLIC soloway-qt-tasktree Qt6::Core Qt6::Gui Qt6::Qml Qt6::Quick Qt6::Network)

qt_add_executable(chiaki-taskui main.cpp)
target_link_libraries(chiaki-taskui
    PRIVATE chiaki-taskui-ui chiaki-taskui-uiplugin)

# Deployment (prod tree): binary + launcher; the tasktree lib installs its own
# lib/qml via the vendored subproject's install rules.
install(TARGETS chiaki-taskui RUNTIME DESTINATION bin)
install(PROGRAMS chiaki-taskui.sh DESTINATION bin)

# Headless functional test of the bridge (import/edit/export round-trip).
find_package(Qt6 QUIET COMPONENTS Test QuickTest)
if(TARGET Qt6::Test)
    enable_testing()
    add_executable(tst_bridge tst_bridge.cpp)
    target_link_libraries(tst_bridge
        PRIVATE chiaki-taskui-ui soloway-qt-tasktree Qt6::Core Qt6::Gui Qt6::Network Qt6::Test)
    add_test(NAME tst_bridge COMMAND tst_bridge)
    set_tests_properties(tst_bridge PROPERTIES
        ENVIRONMENT_MODIFICATION
            "QT_QPA_PLATFORM=set:offscreen;LD_LIBRARY_PATH=path_list_prepend:$<TARGET_FILE_DIR:Qt6::Core>")
endif()

# QML/Quick Test suite for the UI components (tests/tst_*.qml).
if(TARGET Qt6::QuickTest)
    add_subdirectory(tests)
endif()
