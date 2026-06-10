#pragma once
#include <QObject>
#include <QString>
#include <QStringList>
#include <QJsonObject>
#include <QList>
#include <functional>
#include <QtQml/qqmlregistration.h>

#include "TaskTreeModel.h"

class QProcess;
class QFileSystemWatcher;
namespace QtTaskTree { class QTaskTree; }

// ChiakiTaskBridge — glue between the learned PS5 tasks on disk
// (<learningRoot>/<namespace>/tasks.json) and a TaskTreeModel:
//   import: tasks.json -> model (task -> Group node, steps -> child nodes;
//           edits flow to the model's store, e.g. CouchTaskStore -> save to Couch).
//   export: model -> tasks.json (round-trips edits back to the learned store).
//   run:    invokes chiaki_remote_gateway.py run-task --goal <goal> on the live
//           PlayStation session, streaming output.
class ChiakiTaskBridge : public QObject
{
    Q_OBJECT
    QML_ELEMENT
    Q_PROPERTY(QString learningRoot READ learningRoot WRITE setLearningRoot NOTIFY configChanged)
    Q_PROPERTY(QString gatewayScript READ gatewayScript WRITE setGatewayScript NOTIFY configChanged)
    Q_PROPERTY(QString chiakiRoot READ chiakiRoot WRITE setChiakiRoot NOTIFY configChanged)
    Q_PROPERTY(bool running READ running NOTIFY runningChanged)
    Q_PROPERTY(bool chiakiRunning READ chiakiRunning NOTIFY chiakiRunningChanged)
public:
    explicit ChiakiTaskBridge(QObject *parent = nullptr);

    QString learningRoot() const { return m_root; }
    void setLearningRoot(const QString &root);
    QString gatewayScript() const { return m_gateway; }
    void setGatewayScript(const QString &path);
    QString chiakiRoot() const { return m_chiakiRoot; }
    void setChiakiRoot(const QString &root);
    bool running() const;
    bool chiakiRunning() const;

    // Chiaki app lifecycle — direct QProcess for full control over env + handle.
    // Launches <chiakiRoot>/bin/chiaki with the env from bin/chiaki-launch.
    Q_INVOKABLE void launchChiaki();
    Q_INVOKABLE void closeChiaki();
    // Probe the live session via the gateway `status` command (uses QProcessTask).
    Q_INVOKABLE void testConnection(const QString &ns = QString());

    // Namespaces = subdirectories of learningRoot (ps, nhl26, ...).
    Q_INVOKABLE QStringList namespaces() const;

    // Replace the model's contents with the tasks of <namespace>. Returns the
    // number of tasks imported (-1 on read error).
    Q_INVOKABLE int importJson(TaskTreeModel *model, const QString &ns);

    // Serialize the model's task tree back to <namespace>/tasks.json.
    Q_INVOKABLE bool exportJson(TaskTreeModel *model, const QString &ns);

    // Run a learned task on the live session via the gateway.
    Q_INVOKABLE void runTask(const QString &goal, const QString &ns);
    Q_INVOKABLE void stopRun();

    // Watch <ns>/tasks.json; on external write, merge newly-added task keys into
    // the model (live sync of tasks learned by the gateway).
    Q_INVOKABLE void watchNamespace(TaskTreeModel *model, const QString &ns);
    Q_INVOKABLE int mergeJson(TaskTreeModel *model, const QString &ns);

    // Classify the current PlayStation screen via the gateway; emits contextChanged
    // with the detected page (for dynamic-mode filtering).
    Q_INVOKABLE void classify(const QString &ns);

    // Mirror of the gateway slugify (lowercase, non-alnum -> '-').
    Q_INVOKABLE static QString slugify(const QString &text);

signals:
    void configChanged();
    void runningChanged();
    void runOutput(const QString &line);
    void runFinished(bool ok);
    void errorOccurred(const QString &message);
    void contextChanged(const QString &page);
    void tasksMerged(int added);
    void chiakiRunningChanged();
    // replicaAvailable, chiakiRunning, human-readable message.
    void connectionStatus(bool replicaAvailable, bool chiakiRunning, const QString &message);

private:
    QString m_root;
    QString m_gateway;
    QString m_chiakiRoot;
    QProcess *m_chiaki = nullptr;
    QFileSystemWatcher *m_watcher = nullptr;
    TaskTreeModel *m_watchModel = nullptr;
    QString m_watchNs;
    QList<QtTaskTree::QTaskTree *> m_gwTrees;
    QtTaskTree::QTaskTree *m_runTree = nullptr; // active run-task, if any

    QString tasksPath(const QString &ns) const;

    // Run a gateway subcommand as a Qt6::TaskTree QProcessTask. `done` gets stdout
    // + success; optional `line` streams stdout lines as they arrive. Returns the
    // owning task tree (auto-deleted on completion).
    QtTaskTree::QTaskTree *runGateway(const QStringList &args, const QString &ns,
                    std::function<void(const QString &out, bool ok)> done,
                    std::function<void(const QString &line)> line = nullptr);
    QString addTaskFromJson(TaskTreeModel *model, const QString &key,
                            const QJsonObject &task, const QString &ns) const;
};
