#include "ChiakiTaskBridge.h"

#include "TaskTreeModel.h"
#include "TaskItem.h"
#include "TaskEnums.h"

#include <QProcess>
#include <QProcessEnvironment>
#include <QFileSystemWatcher>
#include <QRegularExpression>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonArray>
#include <QDateTime>

#include <QtTaskTree/qtasktree.h>
#include <QtTaskTree/qprocesstask.h>

namespace {
constexpr int kGroup = int(TaskTree::Type::Group);
constexpr int kManual = int(TaskTree::Type::Manual);
} // namespace

ChiakiTaskBridge::ChiakiTaskBridge(QObject *parent) : QObject(parent)
{
    m_root = QDir::homePath() + QStringLiteral("/.local/share/chiaki-remote-gateway/learning");
    // Default gateway: sibling scripts/ of this app's repo.
    m_gateway = QStringLiteral("/run/media/soloway/workspace/Devel/Projects/soloway/apps/ps5/chiaki-ng/hermes-bridge/scripts/chiaki_remote_gateway.py");
    m_chiakiRoot = QStringLiteral("/run/media/soloway/workspace/prod/games/ps/chiaki");

    m_watcher = new QFileSystemWatcher(this);
    connect(m_watcher, &QFileSystemWatcher::fileChanged, this, [this](const QString &path) {
        // Editors rewrite the file (drop the watch); re-add then merge new tasks.
        if (!m_watcher->files().contains(path) && QFile::exists(path))
            m_watcher->addPath(path);
        if (m_watchModel)
            mergeJson(m_watchModel, m_watchNs);
    });
}

void ChiakiTaskBridge::setLearningRoot(const QString &root)
{
    if (m_root == root)
        return;
    m_root = root;
    emit configChanged();
}

void ChiakiTaskBridge::setGatewayScript(const QString &path)
{
    if (m_gateway == path)
        return;
    m_gateway = path;
    emit configChanged();
}

void ChiakiTaskBridge::setChiakiRoot(const QString &root)
{
    if (m_chiakiRoot == root)
        return;
    m_chiakiRoot = root;
    emit configChanged();
}

bool ChiakiTaskBridge::running() const
{
    return m_runTree != nullptr;
}

bool ChiakiTaskBridge::chiakiRunning() const
{
    return m_chiaki && m_chiaki->state() != QProcess::NotRunning;
}

QString ChiakiTaskBridge::tasksPath(const QString &ns) const
{
    return QStringLiteral("%1/%2/tasks.json").arg(m_root, ns);
}

QStringList ChiakiTaskBridge::namespaces() const
{
    QStringList out;
    QDir root(m_root);
    if (!root.exists())
        return out;
    const QStringList dirs = root.entryList(QDir::Dirs | QDir::NoDotAndDotDot, QDir::Name);
    for (const QString &d : dirs)
        if (d != QLatin1String("buffer") && !d.startsWith(QLatin1Char('.')))
            out << d;
    return out;
}

QString ChiakiTaskBridge::slugify(const QString &text)
{
    QString s;
    bool lastDash = false;
    for (const QChar &c : text.toLower()) {
        if (c.isLetterOrNumber()) {
            s.append(c);
            lastDash = false;
        } else if (!lastDash && !s.isEmpty()) {
            s.append(QLatin1Char('-'));
            lastDash = true;
        }
    }
    while (s.endsWith(QLatin1Char('-')))
        s.chop(1);
    return s;
}

int ChiakiTaskBridge::importJson(TaskTreeModel *model, const QString &ns)
{
    if (!model)
        return -1;
    QFile f(tasksPath(ns));
    if (!f.open(QIODevice::ReadOnly)) {
        emit errorOccurred(QStringLiteral("no tasks.json for namespace '%1'").arg(ns));
        return -1;
    }
    const QJsonObject root = QJsonDocument::fromJson(f.readAll()).object();

    // Replace existing content (also clears the backing store).
    const QStringList existing = model->rootIds();
    for (const QString &id : existing)
        model->removeTask(id);

    int count = 0;
    for (auto it = root.begin(); it != root.end(); ++it) {
        addTaskFromJson(model, it.key(), it.value().toObject(), ns);
        ++count;
    }
    return count;
}

QString ChiakiTaskBridge::addTaskFromJson(TaskTreeModel *model, const QString &key,
                                          const QJsonObject &task, const QString &ns) const
{
    const QString goal = task.value(QStringLiteral("goal")).toString(key);
    QVariantMap groupPayload{
        {QStringLiteral("mode"), QStringLiteral("Sequential")},
        {QStringLiteral("key"), task.value(QStringLiteral("key")).toString(key)},
        {QStringLiteral("namespace"), ns},
        {QStringLiteral("updated_at"), task.value(QStringLiteral("updated_at")).toString()},
        // Preconditions + provenance. Learned tasks are pending until approved.
        {QStringLiteral("start_scene"), task.value(QStringLiteral("start_scene")).toString()},
        {QStringLiteral("end_scene"), task.value(QStringLiteral("end_scene")).toString()},
        {QStringLiteral("source"), task.value(QStringLiteral("source")).toString(QStringLiteral("learned"))},
        {QStringLiteral("approved"), task.value(QStringLiteral("approved")).toBool(false)},
        {QStringLiteral("expected_start"), task.value(QStringLiteral("expected_start")).toObject().toVariantMap()},
        {QStringLiteral("expected_end"), task.value(QStringLiteral("expected_end")).toObject().toVariantMap()}};
    const QString gid = model->addTask({}, goal, kGroup, groupPayload);

    const QJsonArray steps = task.value(QStringLiteral("steps")).toArray();
    for (const QJsonValue &sv : steps) {
        const QJsonObject step = sv.toObject();
        const QString name = step.value(QStringLiteral("name")).toString(
            step.value(QStringLiteral("button")).toString(QStringLiteral("step")));
        model->addTask(gid, name, kManual, step.toVariantMap());
    }
    return gid;
}

void ChiakiTaskBridge::watchNamespace(TaskTreeModel *model, const QString &ns)
{
    m_watchModel = model;
    m_watchNs = ns;
    if (!m_watcher->files().isEmpty())
        m_watcher->removePaths(m_watcher->files());
    const QString path = tasksPath(ns);
    if (QFile::exists(path))
        m_watcher->addPath(path);
}

int ChiakiTaskBridge::mergeJson(TaskTreeModel *model, const QString &ns)
{
    if (!model)
        return -1;
    QFile f(tasksPath(ns));
    if (!f.open(QIODevice::ReadOnly))
        return -1;
    const QJsonObject root = QJsonDocument::fromJson(f.readAll()).object();

    // Keys already present (by the task's stored `key`).
    QStringList existing;
    for (const QString &id : model->rootIds())
        existing << model->task(id).payload.value(QStringLiteral("key")).toString();

    int added = 0;
    for (auto it = root.begin(); it != root.end(); ++it) {
        const QString key = it.value().toObject().value(QStringLiteral("key")).toString(it.key());
        if (existing.contains(key))
            continue;
        addTaskFromJson(model, it.key(), it.value().toObject(), ns);
        ++added;
    }
    if (added > 0)
        emit tasksMerged(added);
    return added;
}

void ChiakiTaskBridge::captureExpected(const QString &ns)
{
    const QString dir = QStringLiteral("%1/screenshots/%2").arg(m_root, ns);
    QDir().mkpath(dir);
    const QString png = QStringLiteral("%1/expected-%2.png")
                            .arg(dir).arg(QDateTime::currentSecsSinceEpoch());

    // Capture the live screen, then classify it for the matcher scene label.
    runGateway({QStringLiteral("screenshot"), QStringLiteral("--output"), png}, ns,
               [this, png, ns](const QString &, bool ok) {
        if (!ok)
            emit errorOccurred(QStringLiteral("screenshot failed (no live session?)"));
        runGateway({QStringLiteral("classify")}, ns, [this, png](const QString &out, bool) {
            static const QRegularExpression re(
                QStringLiteral("page[\"']?\\s*[:=]\\s*[\"']([^\"']+)[\"']"));
            const QRegularExpressionMatch m = re.match(out);
            emit expectedCaptured(png, m.hasMatch() ? m.captured(1).trimmed() : QString());
        });
    });
}

void ChiakiTaskBridge::classify(const QString &ns)
{
    runGateway({QStringLiteral("classify")}, ns, [this](const QString &out, bool) {
        // Match `"page": "X"` (JSON) or `page: 'X'` (gateway stderr).
        static const QRegularExpression re(
            QStringLiteral("page[\"']?\\s*[:=]\\s*[\"']([^\"']+)[\"']"));
        const QRegularExpressionMatch m = re.match(out);
        if (m.hasMatch())
            emit contextChanged(m.captured(1).trimmed());
        else
            emit errorOccurred(QStringLiteral("classify: could not determine page"));
    });
}

bool ChiakiTaskBridge::exportJson(TaskTreeModel *model, const QString &ns)
{
    if (!model)
        return false;
    QJsonObject root;
    const QString now = QDateTime::currentDateTimeUtc().toString(QStringLiteral("yyyy-MM-ddTHH:mm:ssZ"));

    for (const QString &id : model->rootIds()) {
        const TaskItem t = model->task(id);
        const QString key = t.payload.value(QStringLiteral("key"), slugify(t.title)).toString();

        QJsonArray steps;
        for (const TaskItem &c : model->children(id)) {
            QJsonObject step = QJsonObject::fromVariantMap(c.payload);
            step.insert(QStringLiteral("name"), c.title);
            steps.append(step);
        }
        root.insert(key, QJsonObject{
            {QStringLiteral("goal"), t.title},
            {QStringLiteral("key"), key},
            {QStringLiteral("namespace"), ns},
            {QStringLiteral("updated_at"), now},
            {QStringLiteral("start_scene"), t.payload.value(QStringLiteral("start_scene")).toString()},
            {QStringLiteral("end_scene"), t.payload.value(QStringLiteral("end_scene")).toString()},
            {QStringLiteral("source"), t.payload.value(QStringLiteral("source"), QStringLiteral("user")).toString()},
            {QStringLiteral("approved"), t.payload.value(QStringLiteral("approved"), true).toBool()},
            {QStringLiteral("expected_start"), QJsonObject::fromVariantMap(t.payload.value(QStringLiteral("expected_start")).toMap())},
            {QStringLiteral("expected_end"), QJsonObject::fromVariantMap(t.payload.value(QStringLiteral("expected_end")).toMap())},
            {QStringLiteral("steps"), steps}});
    }

    const QString path = tasksPath(ns);
    QDir().mkpath(QFileInfo(path).absolutePath());
    QFile f(path);
    if (!f.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
        emit errorOccurred(QStringLiteral("cannot write %1").arg(path));
        return false;
    }
    f.write(QJsonDocument(root).toJson(QJsonDocument::Indented));
    return true;
}

// ── Gateway execution on the Qt6::TaskTree engine (QProcessTask) ─────────────
QtTaskTree::QTaskTree *ChiakiTaskBridge::runGateway(
    const QStringList &args, const QString &ns,
    std::function<void(const QString &, bool)> done,
    std::function<void(const QString &)> line)
{
    using namespace QtTaskTree;
    QStringList full;
    full << m_gateway << args;
    auto *tree = new QTaskTree(this);

    // The done handler gets a const QProcess (can't drain buffers), so accumulate
    // stdout+stderr into a shared buffer as it arrives.
    auto buf = std::make_shared<QString>();

    QProcessTask task(
        [full, ns, line, buf](QProcess &p) {
            p.setProgram(QStringLiteral("python3"));
            p.setArguments(full);
            if (!ns.isEmpty()) {
                QProcessEnvironment env = QProcessEnvironment::systemEnvironment();
                env.insert(QStringLiteral("CHIAKI_LEARNING_NAMESPACE"), ns);
                p.setProcessEnvironment(env);
            }
            QObject::connect(&p, &QProcess::readyReadStandardOutput, &p, [pp = &p, buf, line]() {
                const QString s = QString::fromUtf8(pp->readAllStandardOutput());
                buf->append(s);
                if (line)
                    for (const QString &ln : s.split(QLatin1Char('\n'), Qt::SkipEmptyParts))
                        line(ln.trimmed());
            });
            QObject::connect(&p, &QProcess::readyReadStandardError, &p, [pp = &p, buf]() {
                buf->append(QString::fromUtf8(pp->readAllStandardError()));
            });
        },
        [done, buf](const QProcess &, DoneWith dw) {
            if (done)
                done(*buf, dw == DoneWith::Success);
        });

    tree->setRecipe(Group{task});
    m_gwTrees.append(tree);
    connect(tree, &QTaskTree::done, this, [this, tree](DoneWith) {
        m_gwTrees.removeAll(tree);
        if (m_runTree == tree) {
            m_runTree = nullptr;
            emit runningChanged();
        }
        tree->deleteLater();
    });
    tree->start();
    return tree;
}

void ChiakiTaskBridge::runTask(const QString &goal, const QString &ns)
{
    if (running()) {
        emit errorOccurred(QStringLiteral("a task is already running"));
        return;
    }
    m_runTree = runGateway(
        {QStringLiteral("run-task"), QStringLiteral("--goal"), goal}, ns,
        [this](const QString &, bool ok) { emit runFinished(ok); },
        [this](const QString &l) { emit runOutput(l); });
    emit runningChanged();
}

void ChiakiTaskBridge::stopRun()
{
    if (m_runTree) {
        m_gwTrees.removeAll(m_runTree);
        m_runTree->deleteLater(); // destroying the tree cancels the QProcessTask
        m_runTree = nullptr;
        emit runningChanged();
    }
}

// ── Chiaki app lifecycle (direct QProcess) ──────────────────────────────────
void ChiakiTaskBridge::launchChiaki()
{
    if (chiakiRunning()) {
        emit errorOccurred(QStringLiteral("chiaki already running"));
        return;
    }
    const QString root = m_chiakiRoot;
    QProcessEnvironment env = QProcessEnvironment::systemEnvironment();
    const QString ld = env.value(QStringLiteral("LD_LIBRARY_PATH"));
    env.insert(QStringLiteral("LD_LIBRARY_PATH"),
               ld.isEmpty() ? root + QStringLiteral("/lib")
                            : root + QStringLiteral("/lib:") + ld);
    env.insert(QStringLiteral("QT_PLUGIN_PATH"), root + QStringLiteral("/plugins"));
    env.insert(QStringLiteral("QML2_IMPORT_PATH"), root + QStringLiteral("/qml"));
    env.insert(QStringLiteral("QML_IMPORT_PATH"), root + QStringLiteral("/qml"));
    env.insert(QStringLiteral("QT_QPA_PLATFORM_PLUGIN_PATH"), root + QStringLiteral("/plugins/platforms"));
    env.insert(QStringLiteral("QTWEBENGINEPROCESS_PATH"), root + QStringLiteral("/resources/QtWebEngineProcess"));
    env.insert(QStringLiteral("QT_WEBENGINE_RESOURCES_PATH"), root + QStringLiteral("/resources"));

    m_chiaki = new QProcess(this);
    m_chiaki->setProcessEnvironment(env);
    m_chiaki->setProgram(root + QStringLiteral("/bin/chiaki"));
    connect(m_chiaki, &QProcess::started, this, &ChiakiTaskBridge::chiakiRunningChanged);
    connect(m_chiaki, &QProcess::finished, this, [this](int, QProcess::ExitStatus) {
        emit chiakiRunningChanged();
    });
    m_chiaki->start();
    if (!m_chiaki->waitForStarted(3000))
        emit errorOccurred(QStringLiteral("failed to launch chiaki: ") + m_chiaki->errorString());
}

void ChiakiTaskBridge::closeChiaki()
{
    if (!chiakiRunning()) {
        emit errorOccurred(QStringLiteral("chiaki not running"));
        return;
    }
    m_chiaki->terminate();
    if (!m_chiaki->waitForFinished(3000))
        m_chiaki->kill();
    emit chiakiRunningChanged();
}

void ChiakiTaskBridge::testConnection(const QString &ns)
{
    runGateway({QStringLiteral("status")}, ns, [this](const QString &out, bool) {
        const int a = out.indexOf(QLatin1Char('{'));
        const int b = out.lastIndexOf(QLatin1Char('}'));
        QJsonObject o;
        if (a >= 0 && b > a)
            o = QJsonDocument::fromJson(out.mid(a, b - a + 1).toUtf8()).object();
        const bool replica = o.value(QStringLiteral("replica_available")).toBool();
        const bool crun = o.value(QStringLiteral("chiaki_running")).toBool();
        QString msg = replica ? QStringLiteral("session connected")
                    : crun   ? QStringLiteral("chiaki running, no replica")
                             : QStringLiteral("chiaki not running");
        const QJsonValue err = o.value(QStringLiteral("replica_error"));
        if (!err.isNull() && !err.toString().isEmpty())
            msg += QStringLiteral(" (%1)").arg(err.toString());
        emit connectionStatus(replica, crun, msg);
    });
}
