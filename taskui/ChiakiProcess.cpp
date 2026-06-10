#include "ChiakiProcess.h"

#include <QProcessEnvironment>

#include <sys/resource.h>
#include <unistd.h>

QString ChiakiProcess::defaultRoot()
{
    return QStringLiteral("/run/media/soloway/workspace/prod/games/ps/chiaki");
}

ChiakiProcess::ChiakiProcess(QObject *parent)
    : QProcess(parent)
    , m_root(defaultRoot())
{
    applyRoot();
}

void ChiakiProcess::setChiakiRoot(const QString &root)
{
    if (m_root == root)
        return;
    m_root = root;
    applyRoot();
}

void ChiakiProcess::applyRoot()
{
    // Environment from bin/chiaki-launch, applied natively.
    QProcessEnvironment env = QProcessEnvironment::systemEnvironment();
    const QString ld = env.value(QStringLiteral("LD_LIBRARY_PATH"));
    env.insert(QStringLiteral("LD_LIBRARY_PATH"),
               ld.isEmpty() ? m_root + QStringLiteral("/lib")
                            : m_root + QStringLiteral("/lib:") + ld);
    env.insert(QStringLiteral("QT_PLUGIN_PATH"), m_root + QStringLiteral("/plugins"));
    env.insert(QStringLiteral("QML2_IMPORT_PATH"), m_root + QStringLiteral("/qml"));
    env.insert(QStringLiteral("QML_IMPORT_PATH"), m_root + QStringLiteral("/qml"));
    env.insert(QStringLiteral("QT_QPA_PLATFORM_PLUGIN_PATH"),
               m_root + QStringLiteral("/plugins/platforms"));
    env.insert(QStringLiteral("QTWEBENGINEPROCESS_PATH"),
               m_root + QStringLiteral("/resources/QtWebEngineProcess"));
    env.insert(QStringLiteral("QT_WEBENGINE_RESOURCES_PATH"),
               m_root + QStringLiteral("/resources"));
    setProcessEnvironment(env);
    setProgram(m_root + QStringLiteral("/bin/chiaki"));
    setWorkingDirectory(m_root);

    // Unix child setup (fork/exec hooks):
    //  - clean signal disposition for chiaki's own handlers,
    //  - no inherited fds beyond stdio (keeps the stream process tight),
    //  - own session so closing our controlling terminal doesn't HUP a live
    //    remote-play stream.
    UnixProcessParameters params;
    params.flags = UnixProcessFlag::ResetSignalHandlers
                 | UnixProcessFlag::CloseFileDescriptors
                 | UnixProcessFlag::CreateNewSession;
    params.lowestFileDescriptorToClose = 3; // keep stdin/stdout/stderr
    setUnixProcessParameters(params);

    // Between fork and exec: best-effort priority boost for stream latency.
    // Async-signal-safe only — setpriority is a raw syscall; no Qt/libc heap.
    setChildProcessModifier([] {
        ::setpriority(PRIO_PROCESS, 0, -10); // ignored without privilege
    });
}

void ChiakiProcess::setupGui()
{
    setArguments({});
}

void ChiakiProcess::setupStream(const QString &nickname, const QString &host,
                                const QStringList &extraArgs)
{
    QStringList args{QStringLiteral("stream"), nickname, host};
    args += extraArgs;
    setArguments(args);
}

void ChiakiProcess::setupWakeup(const QString &host, const QString &registKey)
{
    setArguments({QStringLiteral("wakeup"), QStringLiteral("-5"),
                  QStringLiteral("-h"), host,
                  QStringLiteral("-r"), registKey});
}
