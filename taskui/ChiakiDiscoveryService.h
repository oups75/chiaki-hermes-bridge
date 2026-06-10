#pragma once
#include <QObject>
#include <QString>
#include <QVariantList>
#include <QtQml/qqmlregistration.h>

#include <chiaki/discovery.h>
#include <chiaki/log.h>

// ChiakiDiscoveryService — native PS4/PS5 console discovery and wakeup on
// libchiaki (chiaki/discovery.h), replacing subprocess + hand-rolled SRCH:
//   discover(): broadcast (+ cached-host unicast) SRCH; consoleFound() per
//   reply with host/state/name; finished() when the probe window closes.
//   wakeup(): chiaki_discovery_wakeup with the plaintext regist key.
// The last seen console is cached (shared with the python gateway's
// last_console.json) so a discovery-silent console can still be targeted.
class ChiakiDiscoveryService : public QObject
{
    Q_OBJECT
    QML_ELEMENT
    Q_PROPERTY(bool running READ running NOTIFY runningChanged)
    Q_PROPERTY(QString cachedHost READ cachedHost NOTIFY cachedHostChanged)
public:
    explicit ChiakiDiscoveryService(QObject *parent = nullptr);
    ~ChiakiDiscoveryService() override;

    bool running() const { return m_running; }
    QString cachedHost() const { return m_cachedHost; }

    // Probe the LAN for consoles; results accumulate until finished().
    // Virtual so tests can substitute a deterministic fake.
    Q_INVOKABLE virtual bool discover(int timeoutMs = 3000);
    Q_INVOKABLE void stop();

    // Consoles found by the last discover(): [{host, state, name, id}, ...]
    // state: "ready" | "standby" | "unknown".
    Q_INVOKABLE QVariantList consoles() const { return m_consoles; }

    // Send a wakeup packet (PS5). registKey is the plaintext regist key.
    Q_INVOKABLE virtual bool wakeup(const QString &host, const QString &registKey);

signals:
    void runningChanged();
    void cachedHostChanged();
    void consoleFound(const QString &host, const QString &state, const QString &name);
    void finished(int consoleCount);
    void errorOccurred(const QString &message);

protected:
    // For test fakes: seed results and signal completion.
    void setConsolesForTest(const QVariantList &consoles) { m_consoles = consoles; }
    void setCachedHostForTest(const QString &host) { m_cachedHost = host; }

private:
    static void discoveryCb(ChiakiDiscoveryHost *host, void *user);
    void onHostDiscovered(const QVariantMap &console);
    void finishDiscovery();
    QStringList broadcastTargets() const;
    void loadCache();
    void saveCache(const QString &host);

    ChiakiLog m_log {};
    ChiakiDiscovery m_discovery {};
    ChiakiDiscoveryThread m_thread {};
    bool m_running = false;
    QVariantList m_consoles;
    QString m_cachedHost;
};
