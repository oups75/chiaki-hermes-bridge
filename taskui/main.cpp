#include <QGuiApplication>
#include <QQmlApplicationEngine>
#include <QProcessEnvironment>

// chiaki-taskui — QML editor for learned PS5 tasks. The task tree lives in the
// tasktree-mcp server (one authoritative model + store, shared with agents via
// MCP); this UI connects to it over Qt Remote Objects (RemoteTaskClient) and
// can import/export tasks.json and run tasks on the live session via the
// gateway. Override the server URL with TASKTREE_RO_URL.
int main(int argc, char **argv)
{
    QGuiApplication app(argc, argv);
    app.setApplicationName(QStringLiteral("chiaki-taskui"));
    app.setOrganizationName(QStringLiteral("Soloway")); // QML Settings backing file

    // Optional env override; the persisted default lives in QML Settings.
    const QString envUrl = QProcessEnvironment::systemEnvironment()
                               .value(QStringLiteral("TASKTREE_RO_URL"));

    QQmlApplicationEngine engine;
    engine.setInitialProperties({{QStringLiteral("envServerUrl"), envUrl}});
    engine.loadFromModule("ChiakiTaskUi", "Main");
    if (engine.rootObjects().isEmpty())
        return 1;
    return app.exec();
}
