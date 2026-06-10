#include <QGuiApplication>
#include <QQmlApplicationEngine>
#include <QQmlContext>
#include <QProcessEnvironment>

#include "InMemoryTaskStore.h"
#include "CouchTaskStore.h"

// chiaki-taskui — QML editor for learned PS5 tasks. Loads tasks.json, edits,
// saves to CouchDB (when AGENTKIT_COUCHDB_URL is set) and can export back to JSON
// and run a task on the live session via the gateway.
int main(int argc, char **argv)
{
    QGuiApplication app(argc, argv);
    app.setApplicationName(QStringLiteral("chiaki-taskui"));

    // Backing store: CouchDB when configured (save-to-Couch), else in-memory.
    ITaskStore *store = nullptr;
    const QString couch = QProcessEnvironment::systemEnvironment()
                              .value(QStringLiteral("AGENTKIT_COUCHDB_URL"));
    if (!couch.isEmpty())
        store = CouchTaskStore::fromEnv(QStringLiteral("kit_tasks"));
    else
        store = new InMemoryTaskStore;
    store->setParent(&app);
    store->startWatching(); // CouchDB _changes (no-op for in-memory)

    QQmlApplicationEngine engine;
    engine.rootContext()->setContextProperty(QStringLiteral("appStore"), store);
    engine.rootContext()->setContextProperty(
        QStringLiteral("couchConfigured"), !couch.isEmpty());
    engine.loadFromModule("ChiakiTaskUi", "Main");
    if (engine.rootObjects().isEmpty())
        return 1;
    return app.exec();
}
