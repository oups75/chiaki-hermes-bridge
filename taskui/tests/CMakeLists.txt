# Quick Test harness for the taskui QML components. The harness links the
# ChiakiTaskUi module (static lib + plugin) so tst_Main.qml can instantiate
# Main.qml; Soloway.QtTaskTree auto-registers via the linked shared lib.
qt_add_executable(tst_qmltests main.cpp)
target_compile_definitions(tst_qmltests
    PRIVATE QUICK_TEST_SOURCE_DIR="${CMAKE_CURRENT_SOURCE_DIR}")
target_link_libraries(tst_qmltests
    PRIVATE chiaki-taskui-ui chiaki-taskui-uiplugin soloway-qt-tasktree
            Qt6::QuickTest Qt6::Qml Qt6::Quick)
add_test(NAME tst_qmltests COMMAND tst_qmltests)
set_tests_properties(tst_qmltests PROPERTIES
    ENVIRONMENT_MODIFICATION
        "QT_QPA_PLATFORM=set:offscreen;LD_LIBRARY_PATH=path_list_prepend:$<TARGET_FILE_DIR:Qt6::Core>")
