cmake_minimum_required(VERSION 3.21)
project(chiaki-taskui LANGUAGES CXX)

set(CMAKE_AUTOMOC ON)

find_package(Qt6 REQUIRED COMPONENTS Core Gui Qml Quick Network TaskTree)
qt_standard_project_setup()

# Consume the Soloway.QtTaskTree module from the monorepo. Override with
# -DTASKTREE_DIR=<path> if the layout differs. Skip the module's own tests.
set(TASKTREE_DIR
    "/run/media/soloway/workspace/Devel/Projects/soloway/libs/qt/tasktree"
    CACHE PATH "Path to the Soloway.QtTaskTree module source")
set(BUILD_TESTING OFF CACHE BOOL "" FORCE)
add_subdirectory(${TASKTREE_DIR} ${CMAKE_BINARY_DIR}/soloway-qt-tasktree)

qt_add_executable(chiaki-taskui
    main.cpp
    ChiakiTaskBridge.cpp
    ChiakiTaskBridge.h
)
qt_add_qml_module(chiaki-taskui
    URI ChiakiTaskUi
    VERSION 1.0
    QML_FILES
        qml/Main.qml
        qml/StepEditor.qml
)
target_link_libraries(chiaki-taskui
    PRIVATE soloway-qt-tasktree Qt6::Core Qt6::Gui Qt6::Qml Qt6::Quick Qt6::Network)

# Headless functional test of the bridge (import/edit/export round-trip).
find_package(Qt6 QUIET COMPONENTS Test)
if(TARGET Qt6::Test)
    enable_testing()
    add_executable(tst_bridge tst_bridge.cpp ChiakiTaskBridge.cpp ChiakiTaskBridge.h)
    target_link_libraries(tst_bridge
        PRIVATE soloway-qt-tasktree Qt6::Core Qt6::Gui Qt6::Network Qt6::Test)
    add_test(NAME tst_bridge COMMAND tst_bridge)
    set_tests_properties(tst_bridge PROPERTIES
        ENVIRONMENT_MODIFICATION
            "QT_QPA_PLATFORM=set:offscreen;LD_LIBRARY_PATH=path_list_prepend:$<TARGET_FILE_DIR:Qt6::Core>")
endif()
