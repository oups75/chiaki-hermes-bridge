#include "ChiakiTaskBridge.h"

#include "ChiakiProcess.h"
#include "RemoteTaskClient.h"
#include "TaskTreeModel.h" // role enum for replica reads
#include "TaskEnums.h"

#include <QAbstractItemModel>

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
#include <QSettings>
#include <QTimer>
#include <QLoggingCategory>

#include <QtTaskTree/qtasktree.h>
#include <QtTaskTree/qprocesstask.h>

// Filterable logging (QT_LOGGING_RULES="soloway.taskui.*.debug=true"):
//   soloway.taskui.chiaki   — chiaki app/session lifecycle
//   soloway.taskui.gateway  — gateway subprocess invocations + output
//   soloway.taskui.bridge   — import/export/merge/watch
Q_LOGGING_CATEGORY(lcChiaki, "soloway.taskui.chiaki")
Q_LOGGING_CATEGORY(lcGateway, "soloway.taskui.gateway")
Q_LOGGING_CATEGORY(lcBridge, "soloway.taskui.bridge")

namespace {
constexpr int kGroup = int(TaskTree::Type::Group);
constexpr int kManual = int(TaskTree::Type::Manual);

// First JSON object embedded in gateway stdout (logs may precede/follow it).
QJsonObject extractJson(const QString &out)
{
    const int a = out.indexOf(QLatin1Char('{'));
    const int b = out.lastIndexOf(QLatin1Char('}'));
    if (a >= 0 && b > a)
        return QJsonDocument::fromJson(out.mid(a, b - a + 1).toUtf8()).object();
    return {};
}
} // namespace

ChiakiTaskBridge::ChiakiTaskBridge(QObject *parent) : QObject(parent)
{
    m_root = QDir::homePath() + QStringLiteral("/.local/share/chiaki-remote-gateway/learning");
    // Default gateway: sibling scripts/ of this app's repo.
    m_gateway = QStringLiteral("/run/media/soloway/workspace/Devel/Projects/soloway/apps/ps5/chiaki-ng/hermes-bridge/scripts/chiaki_remote_gateway.py");
    m_chiakiRoot = QStringLiteral("/run/media/soloway/workspace/prod/games/ps/chiaki");

    // Poll for externally-started/closed chiaki so the UI reflects a session
    // that ends outside our control (user closed the window, stream dropped).
    auto *liveness = new QTimer(this);
    liveness->setInterval(5000);
    connect(liveness, &QTimer::timeout, this, &ChiakiTaskBridge::refreshChiakiRunning);
    liveness->start();

    m_watcher = new QFileSystemWatcher(this);
    connect(m_watcher, &QFileSystemWatcher::fileChanged, this, [this](const QString &path) {
        // Editors rewrite the file (drop the watch); re-add then merge new tasks.
        if (!m_watcher->files().contains(path) && QFile::exists(path))
            m_watcher->addPath(path);
        if (m_watchClient)
            mergeJson(m_watchClient, m_watchNs);
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
    return (m_chiaki && m_chiaki->state() != QProcess::NotRunning)
        || m_sessionTree != nullptr || m_extRunning;
}

void ChiakiTaskBridge::refreshChiakiRunning()
{
    auto *pg = new QProcess(this);
    connect(pg, &QProcess::finished, this, [this, pg](int code, QProcess::ExitStatus) {
        pg->deleteLater();
        const bool ext = (code == 0); // pgrep exit 0 => match
        if (ext != m_extRunning) {
            m_extRunning = ext;
            qCInfo(lcChiaki) << "external chiaki" << (ext ? "detected" : "gone");
            if (!ext && !chiakiRunning())
                emit connectionStatus(false, false,
                                      QStringLiteral("chiaki closed (session ended)"));
            emit chiakiRunningChanged();
        }
    });
    pg->start(QStringLiteral("pgrep"), {QStringLiteral("-f"), m_chiakiRoot + QStringLiteral("/bin/chiaki")});
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

int ChiakiTaskBridge::importJson(RemoteTaskClient *client, const QString &ns)
{
    if (!client)
        return -1;
    QFile f(tasksPath(ns));
    if (!f.open(QIODevice::ReadOnly)) {
        emit errorOccurred(QStringLiteral("no tasks.json for namespace '%1'").arg(ns));
        return -1;
    }
    const QJsonObject root = QJsonDocument::fromJson(f.readAll()).object();

    // Replace existing content. Root ids come from the local replica; the
    // removals are RPCs the host processes in order, ahead of the adds below.
    const QStringList existing = client->rootIds();
    for (const QString &id : existing)
        client->removeTask(id);

    int count = 0;
    for (auto it = root.begin(); it != root.end(); ++it) {
        addTaskFromJson(client, it.key(), it.value().toObject(), ns);
        ++count;
    }
    qCInfo(lcBridge) << "imported" << count << "task(s) from" << ns
                     << "(replaced" << existing.size() << ")";
    return count;
}

QString ChiakiTaskBridge::addTaskFromJson(RemoteTaskClient *client, const QString &key,
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
    // Blocking add: the steps below need the new group's id as parent.
    const QString gid = client->addTaskBlocking({}, goal, kGroup, groupPayload);
    if (gid.isEmpty())
        return {};

    const QJsonArray steps = task.value(QStringLiteral("steps")).toArray();
    for (const QJsonValue &sv : steps) {
        const QJsonObject step = sv.toObject();
        const QString name = step.value(QStringLiteral("name")).toString(
            step.value(QStringLiteral("button")).toString(QStringLiteral("step")));
        client->addTask(gid, name, kManual, step.toVariantMap());
    }
    return gid;
}

void ChiakiTaskBridge::watchNamespace(RemoteTaskClient *client, const QString &ns)
{
    m_watchClient = client;
    m_watchNs = ns;
    if (!m_watcher->files().isEmpty())
        m_watcher->removePaths(m_watcher->files());
    const QString path = tasksPath(ns);
    if (QFile::exists(path))
        m_watcher->addPath(path);
}

int ChiakiTaskBridge::mergeJson(RemoteTaskClient *client, const QString &ns)
{
    if (!client)
        return -1;
    QFile f(tasksPath(ns));
    if (!f.open(QIODevice::ReadOnly))
        return -1;
    const QJsonObject root = QJsonDocument::fromJson(f.readAll()).object();

    // Keys already present (by the task's stored `key`), read from the replica.
    QStringList existing;
    for (const QString &id : client->rootIds())
        existing << client->taskInfo(id).value(QStringLiteral("payload")).toMap()
                       .value(QStringLiteral("key")).toString();

    int added = 0;
    for (auto it = root.begin(); it != root.end(); ++it) {
        const QString key = it.value().toObject().value(QStringLiteral("key")).toString(it.key());
        if (existing.contains(key))
            continue;
        addTaskFromJson(client, it.key(), it.value().toObject(), ns);
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
    if (!chiakiRunning())
        qCWarning(lcGateway) << "classify requested but chiaki appears not to be running";
    runGateway({QStringLiteral("classify")}, ns, [this](const QString &out, bool) {
        // Match `"page": "X"` (JSON) or `page: 'X'` (gateway stderr).
        static const QRegularExpression re(
            QStringLiteral("page[\"']?\\s*[:=]\\s*[\"']([^\"']+)[\"']"));
        const QRegularExpressionMatch m = re.match(out);
        if (m.hasMatch()) {
            emit contextChanged(m.captured(1).trimmed());
            return;
        }
        // Surface the gateway's own diagnosis (e.g. "no decoded stream frame
        // cached" when the session is down) instead of a generic failure.
        const QJsonObject o = extractJson(out);
        QString why = o.value(QStringLiteral("diagnosis")).toString();
        if (why.isEmpty())
            why = o.value(QStringLiteral("error")).toString();
        if (why.isEmpty()) // classify nests details under "details"/"capture"
            for (const QJsonValue &v : o)
                if (v.isObject() && v.toObject().contains(QStringLiteral("diagnosis"))) {
                    why = v.toObject().value(QStringLiteral("diagnosis")).toString();
                    break;
                }
        qCWarning(lcGateway).noquote() << "classify failed, raw output:" << out.trimmed().left(800);
        emit errorOccurred(why.isEmpty()
                               ? QStringLiteral("classify: could not determine page")
                               : QStringLiteral("classify: %1").arg(why));
    });
}

bool ChiakiTaskBridge::exportJson(RemoteTaskClient *client, const QString &ns)
{
    // Serialize the client's live model replica (the user's view of the tree).
    QAbstractItemModel *m = client ? client->model() : nullptr;
    if (!m)
        return false;
    QJsonObject root;
    const QString now = QDateTime::currentDateTimeUtc().toString(QStringLiteral("yyyy-MM-ddTHH:mm:ssZ"));

    for (int r = 0; r < m->rowCount(); ++r) {
        const QModelIndex idx = m->index(r, 0);
        const QString title = m->data(idx, TaskTreeModel::TitleRole).toString();
        const QVariantMap payload = m->data(idx, TaskTreeModel::PayloadRole).toMap();
        const QString key = payload.value(QStringLiteral("key"), slugify(title)).toString();

        QJsonArray steps;
        for (int c = 0; c < m->rowCount(idx); ++c) {
            const QModelIndex cidx = m->index(c, 0, idx);
            QJsonObject step = QJsonObject::fromVariantMap(
                m->data(cidx, TaskTreeModel::PayloadRole).toMap());
            step.insert(QStringLiteral("name"),
                        m->data(cidx, TaskTreeModel::TitleRole).toString());
            steps.append(step);
        }
        root.insert(key, QJsonObject{
            {QStringLiteral("goal"), title},
            {QStringLiteral("key"), key},
            {QStringLiteral("namespace"), ns},
            {QStringLiteral("updated_at"), now},
            {QStringLiteral("start_scene"), payload.value(QStringLiteral("start_scene")).toString()},
            {QStringLiteral("end_scene"), payload.value(QStringLiteral("end_scene")).toString()},
            {QStringLiteral("source"), payload.value(QStringLiteral("source"), QStringLiteral("user")).toString()},
            {QStringLiteral("approved"), payload.value(QStringLiteral("approved"), true).toBool()},
            {QStringLiteral("expected_start"), QJsonObject::fromVariantMap(payload.value(QStringLiteral("expected_start")).toMap())},
            {QStringLiteral("expected_end"), QJsonObject::fromVariantMap(payload.value(QStringLiteral("expected_end")).toMap())},
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
    qCDebug(lcGateway) << "exec python3" << full;
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
        [done, buf, args](const QProcess &, DoneWith dw) {
            qCDebug(lcGateway).noquote() << args.value(args.size() - 1) << "done," << dw
                                         << "output:" << buf->trimmed().left(2000);
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

// ── Chiaki app lifecycle (ChiakiProcess) ────────────────────────────────────
void ChiakiTaskBridge::launchChiaki()
{
    // Adopt an externally-started chiaki rather than spawning a second one.
    if (QProcess::execute(QStringLiteral("pgrep"),
                          {QStringLiteral("-f"), m_chiakiRoot + QStringLiteral("/bin/chiaki")}) == 0) {
        if (!m_extRunning) { m_extRunning = true; emit chiakiRunningChanged(); }
        emit errorOccurred(QStringLiteral("adopted already-running chiaki"));
        return;
    }
    if (chiakiRunning()) {
        emit errorOccurred(QStringLiteral("chiaki already running"));
        return;
    }

    // ChiakiProcess carries the env + unix child setup; just pick GUI mode.
    m_chiaki = new ChiakiProcess(this);
    m_chiaki->setChiakiRoot(m_chiakiRoot);
    m_chiaki->setupGui();
    connect(m_chiaki, &QProcess::started, this, [this] {
        qCInfo(lcChiaki) << "chiaki GUI started, pid" << m_chiaki->processId();
        emit chiakiRunningChanged();
    });
    connect(m_chiaki, &QProcess::finished, this, [this](int code, QProcess::ExitStatus st) {
        qCInfo(lcChiaki) << "chiaki GUI exited, code" << code << st;
        emit connectionStatus(false, chiakiRunning(),
                              QStringLiteral("chiaki exited (code %1)").arg(code));
        emit chiakiRunningChanged();
    });
    qCInfo(lcChiaki) << "launching chiaki GUI from" << m_chiakiRoot;
    m_chiaki->start();
    if (!m_chiaki->waitForStarted(3000))
        emit errorOccurred(QStringLiteral("failed to launch chiaki: ") + m_chiaki->errorString());
}

void ChiakiTaskBridge::launchSession(const QString &nickname, const QString &host,
                                     const QStringList &extraArgs)
{
    using namespace QtTaskTree;
    if (chiakiRunning()) {
        emit errorOccurred(QStringLiteral("chiaki already running"));
        return;
    }
    if (nickname.isEmpty() || host.isEmpty()) {
        emit errorOccurred(QStringLiteral("launchSession needs nickname + host"));
        return;
    }

    // Direct CLI session as a Qt6::TaskTree task: the ChiakiProcessTask setup
    // handler configures the (default-constructed) ChiakiProcess; the tree owns
    // the process for the lifetime of the stream.
    auto *tree = new QTaskTree(this);
    const QString root = m_chiakiRoot;
    auto err = std::make_shared<QString>(); // done handler gets a const process
    ChiakiProcessTask task(
        [root, nickname, host, extraArgs, err](ChiakiProcess &p) {
            p.setChiakiRoot(root);
            p.setupStream(nickname, host, extraArgs);
            QObject::connect(&p, &QProcess::readyReadStandardError, &p, [pp = &p, err]() {
                err->append(QString::fromUtf8(pp->readAllStandardError()));
            });
        },
        [this, err](const ChiakiProcess &, DoneWith dw) {
            if (dw != DoneWith::Success)
                emit errorOccurred(QStringLiteral("chiaki stream ended: ") + err->trimmed());
        });
    tree->setRecipe(Group{task});
    m_sessionTree = tree;
    connect(tree, &QTaskTree::done, this, [this, tree](DoneWith dw) {
        qCInfo(lcChiaki) << "stream session ended," << dw;
        if (m_sessionTree == tree)
            m_sessionTree = nullptr;
        tree->deleteLater();
        emit connectionStatus(false, chiakiRunning(),
                              QStringLiteral("stream session ended"));
        emit chiakiRunningChanged();
    });
    qCInfo(lcChiaki) << "starting CLI stream" << nickname << host << extraArgs;
    tree->start();
    emit chiakiRunningChanged();
}

namespace {
// Registered-console credentials from the chiaki GUI config
// (~/.config/Chiaki/Chiaki.conf, [registered_hosts] array).
struct ConsoleCreds {
    QString nickname;
    QString registKey;
};
ConsoleCreds consoleCreds()
{
    // Native format: on Linux this is ~/.config/Chiaki/Chiaki.conf (the
    // explicit IniFormat would look for Chiaki.ini instead).
    QSettings s(QStringLiteral("Chiaki"), QStringLiteral("Chiaki"));
    ConsoleCreds creds;
    const int n = s.beginReadArray(QStringLiteral("registered_hosts"));
    if (n > 0) {
        s.setArrayIndex(0);
        creds.nickname = s.value(QStringLiteral("server_nickname")).toString();
        // Plaintext key, zero-padded in the stored ByteArray.
        QByteArray key = s.value(QStringLiteral("rp_regist_key")).toByteArray();
        if (const int z = key.indexOf('\0'); z >= 0)
            key.truncate(z);
        creds.registKey = QString::fromLatin1(key);
    }
    s.endArray();
    return creds;
}
} // namespace

void ChiakiTaskBridge::startSession(const QString &ns)
{
    if (chiakiRunning()) {
        emit errorOccurred(QStringLiteral("close chiaki before starting a session"));
        return;
    }
    const ConsoleCreds creds = consoleCreds();
    if (creds.nickname.isEmpty()) {
        emit errorOccurred(QStringLiteral("no registered console in Chiaki.conf"));
        return;
    }

    runGateway({QStringLiteral("discover-console")}, ns, [this, ns, creds](const QString &out, bool) {
        const QJsonArray consoles = extractJson(out).value(QStringLiteral("consoles")).toArray();
        if (consoles.isEmpty()) {
            m_sessionAttempts = 0;
            emit errorOccurred(QStringLiteral(
                "no PS5 found on the LAN (powered off or network disabled in rest mode)"));
            return;
        }
        const QJsonObject c = consoles.first().toObject();
        const QString host = c.value(QStringLiteral("host")).toString();
        const QString state = c.value(QStringLiteral("state")).toString();

        if (state == QLatin1String("ready")) {
            m_sessionAttempts = 0;
            emit runOutput(QStringLiteral("PS5 %1 ready — starting stream").arg(host));
            launchSession(creds.nickname, host);
            return;
        }

        // Standby: send a wakeup, then re-discover until ready (bounded).
        if (m_sessionAttempts >= 8) {
            m_sessionAttempts = 0;
            emit errorOccurred(QStringLiteral("PS5 %1 did not wake up").arg(host));
            return;
        }
        ++m_sessionAttempts;
        emit runOutput(QStringLiteral("PS5 %1 in standby — waking (attempt %2/8)")
                           .arg(host).arg(m_sessionAttempts));
        auto *wake = new ChiakiProcess(this);
        wake->setChiakiRoot(m_chiakiRoot);
        wake->setupWakeup(host, creds.registKey);
        connect(wake, &QProcess::finished, wake, &QObject::deleteLater);
        wake->start();
        QTimer::singleShot(5000, this, [this, ns] { startSession(ns); });
    });
}

void ChiakiTaskBridge::closeChiaki()
{
    if (!chiakiRunning()) {
        emit errorOccurred(QStringLiteral("chiaki not running"));
        return;
    }
    qCInfo(lcChiaki) << "closeChiaki: owned" << (m_chiaki != nullptr)
                     << "session" << (m_sessionTree != nullptr) << "external" << m_extRunning;
    if (m_sessionTree) {
        // Destroying the tree cancels the ChiakiProcessTask -> kills the stream.
        auto *tree = m_sessionTree;
        m_sessionTree = nullptr;
        tree->disconnect(this);
        delete tree;
    }
    if (m_chiaki && m_chiaki->state() != QProcess::NotRunning) {
        m_chiaki->terminate();
        if (!m_chiaki->waitForFinished(3000))
            m_chiaki->kill();
    } else if (m_extRunning) {
        // Externally-started chiaki: terminate by binary path.
        QProcess::execute(QStringLiteral("pkill"),
                          {QStringLiteral("-f"), m_chiakiRoot + QStringLiteral("/bin/chiaki")});
        m_extRunning = false;
    }
    emit chiakiRunningChanged();
}

void ChiakiTaskBridge::restartChiaki()
{
    closeChiaki();
    launchChiaki();
}

// `replica_available` only means chiaki's RemoteController source answers —
// the stream is live only when `session_connected` is true (decoded frames).
void ChiakiTaskBridge::connectSession(const QString &ns)
{
    runGateway({QStringLiteral("wait-session")}, ns, [this](const QString &out, bool) {
        const QJsonObject o = extractJson(out);
        const bool sess = o.value(QStringLiteral("session_connected")).toBool();
        const bool crun = o.value(QStringLiteral("replica_available")).toBool();
        if (crun && !m_extRunning) { m_extRunning = true; emit chiakiRunningChanged(); }
        const QString msg = sess ? QStringLiteral("session connected")
                                 : QStringLiteral("no PS session (start the stream on chiaki)");
        emit connectionStatus(sess, crun, msg);
    });
}

void ChiakiTaskBridge::testConnection(const QString &ns)
{
    // Short wait-session probe: the truthful signal of a live stream is
    // session_connected, not the replica answering (chiaki idles with the
    // RemoteController source up and no decoded frame).
    runGateway({QStringLiteral("--timeout-ms"), QStringLiteral("3000"),
                QStringLiteral("wait-session")}, ns, [this](const QString &out, bool) {
        const QJsonObject o = extractJson(out);
        const bool sess = o.value(QStringLiteral("session_connected")).toBool();
        const bool crun = o.value(QStringLiteral("replica_available")).toBool();
        if (crun != m_extRunning) { m_extRunning = crun; emit chiakiRunningChanged(); }
        QString msg = sess ? QStringLiteral("session connected")
                    : crun ? QStringLiteral("chiaki running, no live session — restart the stream")
                           : QStringLiteral("chiaki not running");
        qCInfo(lcChiaki) << "testConnection: session" << sess << "chiaki" << crun;
        emit connectionStatus(sess, crun, msg);
    });
}
