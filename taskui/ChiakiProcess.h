#pragma once
#include <QProcess>
#include <QString>
#include <QStringList>

#include <QtTaskTree/qprocesstask.h>

// ChiakiProcess — a QProcess pre-configured to launch the bundled chiaki build
// (<chiakiRoot>/bin/chiaki) with the runtime environment from bin/chiaki-launch
// (LD_LIBRARY_PATH, QT_PLUGIN_PATH, QML import paths, WebEngine resources).
//
// Child-process setup uses the Qt 6 native unix hooks instead of a wrapper
// script:
//   - setUnixProcessParameters(): ResetSignalHandlers (clean slate for chiaki's
//     own handlers), CloseFileDescriptors above stdio (no fd leaks into the
//     stream process), CreateNewSession (chiaki survives our terminal's SIGHUP).
//   - setChildProcessModifier(): best-effort nice(-10) — runs between fork and
//     exec in the child, so the priority boost applies before chiaki's threads
//     spawn. Silently ignored without privilege.
//
// Default-constructible, so it slots into Qt6::TaskTree recipes via
// ChiakiProcessTask (the QProcessTask adapter takes any QProcess subclass).
//
// Two launch shapes:
//   setupGui()                       — chiaki            (full GUI, user picks console)
//   setupStream(nickname, host, ..) — chiaki stream N H  (direct CLI session launch)
class ChiakiProcess : public QProcess
{
    Q_OBJECT
public:
    explicit ChiakiProcess(QObject *parent = nullptr);

    QString chiakiRoot() const { return m_root; }
    void setChiakiRoot(const QString &root); // re-applies env + program

    // Launch the chiaki GUI (no arguments).
    void setupGui();

    // Launch a remote-play session directly from the CLI:
    //   chiaki stream <nickname> <host> [extraArgs...]
    // extraArgs e.g. {"--fullscreen"}, {"--zoom"}, {"--exit-app-on-stream-exit"}.
    void setupStream(const QString &nickname, const QString &host,
                     const QStringList &extraArgs = {});

    // Wake a standby console: chiaki wakeup -5 -h <host> -r <registKey>.
    void setupWakeup(const QString &host, const QString &registKey);

    static QString defaultRoot();

private:
    void applyRoot(); // env + program + unix params from m_root

    QString m_root;
};

// Qt6::TaskTree task over ChiakiProcess — same adapter/deleter as QProcessTask,
// so it behaves identically inside a Group recipe:
//   ChiakiProcessTask([](ChiakiProcess &p) { p.setupStream("PS5-123", "10.0.0.7"); },
//                     [](const ChiakiProcess &, QtTaskTree::DoneWith) { ... })
using ChiakiProcessTask = QtTaskTree::QCustomTask<ChiakiProcess,
                                                  QtTaskTree::QProcessTaskAdapter,
                                                  QtTaskTree::QProcessTaskDeleter>;
