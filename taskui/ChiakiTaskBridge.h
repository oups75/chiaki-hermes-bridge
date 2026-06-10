#pragma once
#include <QObject>
#include <QString>
#include <QStringList>
#include <QtQml/qqmlregistration.h>

#include "TaskTreeModel.h"

class QProcess;

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
    Q_PROPERTY(bool running READ running NOTIFY runningChanged)
public:
    explicit ChiakiTaskBridge(QObject *parent = nullptr);

    QString learningRoot() const { return m_root; }
    void setLearningRoot(const QString &root);
    QString gatewayScript() const { return m_gateway; }
    void setGatewayScript(const QString &path);
    bool running() const;

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

    // Mirror of the gateway slugify (lowercase, non-alnum -> '-').
    Q_INVOKABLE static QString slugify(const QString &text);

signals:
    void configChanged();
    void runningChanged();
    void runOutput(const QString &line);
    void runFinished(bool ok);
    void errorOccurred(const QString &message);

private:
    QString m_root;
    QString m_gateway;
    QProcess *m_proc = nullptr;

    QString tasksPath(const QString &ns) const;
};
