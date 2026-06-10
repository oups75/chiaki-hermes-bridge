#pragma once
#include <QObject>
#include <QString>
#include <QStringList>
#include <QJsonObject>
#include <QList>
#include <functional>
#include <QtQml/qqmlregistration.h>

#include "RemoteTaskClient.h"

class ChiakiDiscoveryService;
class ChiakiProcess;
class QFileSystemWatcher;
namespace QtTaskTree { class QTaskTree; }

// ChiakiTaskBridge — glue between the learned PS5 tasks on disk
// (<learningRoot>/<namespace>/tasks.json) and the shared task tree served by
// tasktree-mcp over Qt Remote Objects (RemoteTaskClient):
//   import: tasks.json -> remote tree (task -> Group node, steps -> children;
//           mutations are service RPCs, the host persists via its store).
//   export: the client's live model replica -> tasks.json.
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

    // Chiaki app lifecycle — ChiakiProcess (QProcess subclass) for full control
    // over env + unix child setup. Launches <chiakiRoot>/bin/chiaki with the env
    // from bin/chiaki-launch. An externally-started chiaki (matching the binary)
    // is adopted: detected, reported as running, and closable.
    Q_INVOKABLE void launchChiaki();
    Q_INVOKABLE void closeChiaki();
    Q_INVOKABLE void restartChiaki(); // close + launch
    // Launch a remote-play session directly via the chiaki CLI
    // (`chiaki stream <nickname> <host>`), run as a ChiakiProcessTask on a
    // Qt6::TaskTree. Reuses the same launched-process plumbing as launchChiaki.
    Q_INVOKABLE void launchSession(const QString &nickname, const QString &host,
                                   const QStringList &extraArgs = {});
    // One-button session bootstrap: discover the PS5 on the LAN (gateway
    // discover-console -> IP + power state), wake it if it is in standby
    // (chiaki wakeup, retried with re-discovery), then stream directly
    // (launchSession). Credentials (nickname, regist key) come from the
    // chiaki GUI config (~/.config/Chiaki/Chiaki.conf).
    Q_INVOKABLE void startSession(const QString &ns = QString());
    // Establish/await the PS stream via gateway `wait-session` (uses QProcessTask).
    Q_INVOKABLE void connectSession(const QString &ns = QString());
    // Probe the live session via the gateway `status` command (uses QProcessTask).
    Q_INVOKABLE void testConnection(const QString &ns = QString());
    // Refresh the cached external-chiaki-running flag (async pgrep).
    Q_INVOKABLE void refreshChiakiRunning();

    // Namespaces = subdirectories of learningRoot (ps, nhl26, ...).
    Q_INVOKABLE QStringList namespaces() const;

    // Replace the model's contents with the tasks of <namespace>. Returns the
    // number of tasks imported (-1 on read error).
    Q_INVOKABLE int importJson(RemoteTaskClient *client, const QString &ns);

    // Serialize the model's task tree back to <namespace>/tasks.json.
    Q_INVOKABLE bool exportJson(RemoteTaskClient *client, const QString &ns);

    // Run a learned task on the live session via the gateway.
    Q_INVOKABLE void runTask(const QString &goal, const QString &ns);
    Q_INVOKABLE void stopRun();

    // Watch <ns>/tasks.json; on external write, merge newly-added task keys into
    // the model (live sync of tasks learned by the gateway).
    Q_INVOKABLE void watchNamespace(RemoteTaskClient *client, const QString &ns);
    Q_INVOKABLE int mergeJson(RemoteTaskClient *client, const QString &ns);

    // Classify the current PlayStation screen via the gateway; emits contextChanged
    // with the detected page (for dynamic-mode filtering).
    Q_INVOKABLE void classify(const QString &ns);

    // Capture an expected-state screenshot of the live screen and classify it.
    // Emits expectedCaptured(screenshotPath, sceneLabel). The scene label is what
    // a verifier matches against (classifier-based, tolerant of dynamic content).
    Q_INVOKABLE void captureExpected(const QString &ns);

    // Mirror of the gateway slugify (lowercase, non-alnum -> '-').
    Q_INVOKABLE static QString slugify(const QString &text);

    // Dependency injection for tests: substitute the discovery service used
    // by startSession(). The bridge takes ownership.
    void setDiscoveryService(ChiakiDiscoveryService *service);

signals:
    void configChanged();
    void runningChanged();
    void runOutput(const QString &line);
    void runFinished(bool ok);
    void errorOccurred(const QString &message);
    void contextChanged(const QString &page);
    void tasksMerged(int added);
    void expectedCaptured(const QString &screenshot, const QString &scene);
    void chiakiRunningChanged();
    // replicaAvailable, chiakiRunning, human-readable message.
    void connectionStatus(bool replicaAvailable, bool chiakiRunning, const QString &message);

private:
    QString m_root;
    QString m_gateway;
    QString m_chiakiRoot;
    ChiakiProcess *m_chiaki = nullptr;
    ChiakiDiscoveryService *m_discovery = nullptr; // native libchiaki discovery
    bool m_extRunning = false; // an externally-started chiaki detected via pgrep
    int m_sessionAttempts = 0; // wakeup retries within startSession()
    QFileSystemWatcher *m_watcher = nullptr;
    RemoteTaskClient *m_watchClient = nullptr;
    QString m_watchNs;
    QList<QtTaskTree::QTaskTree *> m_gwTrees;
    QtTaskTree::QTaskTree *m_runTree = nullptr; // active run-task, if any
    QtTaskTree::QTaskTree *m_sessionTree = nullptr; // active CLI stream session

    QString tasksPath(const QString &ns) const;

    // Run a gateway subcommand as a Qt6::TaskTree QProcessTask. `done` gets stdout
    // + success; optional `line` streams stdout lines as they arrive. Returns the
    // owning task tree (auto-deleted on completion).
    QtTaskTree::QTaskTree *runGateway(const QStringList &args, const QString &ns,
                    std::function<void(const QString &out, bool ok)> done,
                    std::function<void(const QString &line)> line = nullptr);
    QString addTaskFromJson(RemoteTaskClient *client, const QString &key,
                            const QJsonObject &task, const QString &ns) const;
};
