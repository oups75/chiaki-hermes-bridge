#include "ChiakiTaskBridge.h"

#include "TaskTreeModel.h"
#include "TaskItem.h"
#include "TaskEnums.h"

#include <QProcess>
#include <QProcessEnvironment>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonArray>
#include <QDateTime>

namespace {
constexpr int kGroup = int(TaskTree::Type::Group);
constexpr int kManual = int(TaskTree::Type::Manual);
} // namespace

ChiakiTaskBridge::ChiakiTaskBridge(QObject *parent) : QObject(parent)
{
    m_root = QDir::homePath() + QStringLiteral("/.local/share/chiaki-remote-gateway/learning");
    // Default gateway: sibling scripts/ of this app's repo.
    m_gateway = QStringLiteral("/run/media/soloway/workspace/Devel/Projects/soloway/apps/ps5/chiaki-ng/hermes-bridge/scripts/chiaki_remote_gateway.py");
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

bool ChiakiTaskBridge::running() const
{
    return m_proc && m_proc->state() != QProcess::NotRunning;
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
        const QJsonObject task = it.value().toObject();
        const QString goal = task.value(QStringLiteral("goal")).toString(it.key());
        QVariantMap groupPayload{
            {QStringLiteral("mode"), QStringLiteral("Sequential")},
            {QStringLiteral("key"), task.value(QStringLiteral("key")).toString(it.key())},
            {QStringLiteral("namespace"), ns},
            {QStringLiteral("updated_at"), task.value(QStringLiteral("updated_at")).toString()}};
        const QString gid = model->addTask({}, goal, kGroup, groupPayload);

        const QJsonArray steps = task.value(QStringLiteral("steps")).toArray();
        for (const QJsonValue &sv : steps) {
            const QJsonObject step = sv.toObject();
            const QString name = step.value(QStringLiteral("name")).toString(
                step.value(QStringLiteral("button")).toString(QStringLiteral("step")));
            model->addTask(gid, name, kManual, step.toVariantMap());
        }
        ++count;
    }
    return count;
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

void ChiakiTaskBridge::runTask(const QString &goal, const QString &ns)
{
    if (running()) {
        emit errorOccurred(QStringLiteral("a task is already running"));
        return;
    }
    m_proc = new QProcess(this);
    QProcessEnvironment env = QProcessEnvironment::systemEnvironment();
    env.insert(QStringLiteral("CHIAKI_LEARNING_NAMESPACE"), ns);
    m_proc->setProcessEnvironment(env);
    m_proc->setProcessChannelMode(QProcess::MergedChannels);

    connect(m_proc, &QProcess::readyReadStandardOutput, this, [this]() {
        while (m_proc->canReadLine())
            emit runOutput(QString::fromUtf8(m_proc->readLine()).trimmed());
    });
    connect(m_proc, &QProcess::finished, this, [this](int code, QProcess::ExitStatus) {
        emit runFinished(code == 0);
        emit runningChanged();
        m_proc->deleteLater();
        m_proc = nullptr;
    });

    m_proc->start(QStringLiteral("python3"),
                  {m_gateway, QStringLiteral("run-task"), QStringLiteral("--goal"), goal});
    emit runningChanged();
}

void ChiakiTaskBridge::stopRun()
{
    if (m_proc && m_proc->state() != QProcess::NotRunning)
        m_proc->terminate();
}
