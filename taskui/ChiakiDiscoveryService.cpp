#include "ChiakiDiscoveryService.h"

#include <QDir>
#include <QFile>
#include <QJsonDocument>
#include <QJsonObject>
#include <QDateTime>
#include <QLoggingCategory>
#include <QNetworkInterface>
#include <QTimer>

#include <netinet/in.h>
#include <arpa/inet.h>

// QT_LOGGING_RULES="soloway.taskui.discovery.debug=true"
Q_LOGGING_CATEGORY(lcDisco, "soloway.taskui.discovery")

namespace {
QString cachePath()
{
    return QDir::homePath()
         + QStringLiteral("/.local/share/chiaki-remote-gateway/last_console.json");
}
} // namespace

ChiakiDiscoveryService::ChiakiDiscoveryService(QObject *parent) : QObject(parent)
{
    chiaki_log_init(&m_log, 0, nullptr, nullptr); // quiet; we log via Qt
    loadCache();
}

ChiakiDiscoveryService::~ChiakiDiscoveryService()
{
    stop();
}

void ChiakiDiscoveryService::loadCache()
{
    QFile f(cachePath());
    if (!f.open(QIODevice::ReadOnly))
        return;
    const QString host = QJsonDocument::fromJson(f.readAll()).object()
                             .value(QStringLiteral("host")).toString();
    if (!host.isEmpty()) {
        m_cachedHost = host;
        emit cachedHostChanged();
    }
}

void ChiakiDiscoveryService::saveCache(const QString &host)
{
    QDir().mkpath(QFileInfo(cachePath()).absolutePath());
    QFile f(cachePath());
    if (f.open(QIODevice::WriteOnly | QIODevice::Truncate))
        f.write(QJsonDocument(QJsonObject{
            {QStringLiteral("host"), host},
            {QStringLiteral("saved_at"), QDateTime::currentSecsSinceEpoch()},
        }).toJson(QJsonDocument::Compact));
    if (m_cachedHost != host) {
        m_cachedHost = host;
        emit cachedHostChanged();
    }
}

QStringList ChiakiDiscoveryService::broadcastTargets() const
{
    QStringList out{QStringLiteral("255.255.255.255")};
    const auto ifaces = QNetworkInterface::allInterfaces();
    for (const QNetworkInterface &iface : ifaces) {
        if (!(iface.flags() & QNetworkInterface::IsUp)
            || (iface.flags() & QNetworkInterface::IsLoopBack))
            continue;
        const auto entries = iface.addressEntries();
        for (const QNetworkAddressEntry &e : entries)
            if (!e.broadcast().isNull())
                out << e.broadcast().toString();
    }
    if (!m_cachedHost.isEmpty())
        out << m_cachedHost; // unicast survives broadcast filtering
    out.removeDuplicates();
    return out;
}

void ChiakiDiscoveryService::discoveryCb(ChiakiDiscoveryHost *host, void *user)
{
    // libchiaki thread — copy the strings, marshal to the object thread.
    auto *self = static_cast<ChiakiDiscoveryService *>(user);
    QVariantMap console{
        {QStringLiteral("host"), QString::fromUtf8(host->host_addr ? host->host_addr : "")},
        {QStringLiteral("state"),
         host->state == CHIAKI_DISCOVERY_HOST_STATE_READY   ? QStringLiteral("ready")
         : host->state == CHIAKI_DISCOVERY_HOST_STATE_STANDBY ? QStringLiteral("standby")
                                                              : QStringLiteral("unknown")},
        {QStringLiteral("name"), QString::fromUtf8(host->host_name ? host->host_name : "")},
        {QStringLiteral("id"), QString::fromUtf8(host->host_id ? host->host_id : "")},
    };
    QMetaObject::invokeMethod(self, [self, console] { self->onHostDiscovered(console); },
                              Qt::QueuedConnection);
}

void ChiakiDiscoveryService::onHostDiscovered(const QVariantMap &console)
{
    const QString host = console.value(QStringLiteral("host")).toString();
    for (const QVariant &v : std::as_const(m_consoles))
        if (v.toMap().value(QStringLiteral("host")).toString() == host)
            return; // duplicate reply
    m_consoles.append(console);
    if (!host.isEmpty())
        saveCache(host);
    qCInfo(lcDisco) << "console" << host
                    << console.value(QStringLiteral("state")).toString()
                    << console.value(QStringLiteral("name")).toString();
    emit consoleFound(host, console.value(QStringLiteral("state")).toString(),
                      console.value(QStringLiteral("name")).toString());
}

bool ChiakiDiscoveryService::discover(int timeoutMs)
{
    if (m_running) {
        emit errorOccurred(QStringLiteral("discovery already running"));
        return false;
    }
    m_consoles.clear();

    if (chiaki_discovery_init(&m_discovery, &m_log, AF_INET) != CHIAKI_ERR_SUCCESS) {
        emit errorOccurred(QStringLiteral("discovery socket init failed"));
        return false;
    }
    if (chiaki_discovery_thread_start(&m_thread, &m_discovery, discoveryCb, this)
        != CHIAKI_ERR_SUCCESS) {
        chiaki_discovery_fini(&m_discovery);
        emit errorOccurred(QStringLiteral("discovery thread start failed"));
        return false;
    }
    m_running = true;
    emit runningChanged();

    // SRCH to every broadcast domain + the cached console.
    ChiakiDiscoveryPacket packet {};
    packet.cmd = CHIAKI_DISCOVERY_CMD_SRCH;
    const QStringList targets = broadcastTargets();
    qCDebug(lcDisco) << "SRCH to" << targets;
    for (const QString &target : targets) {
        struct sockaddr_in addr {};
        addr.sin_family = AF_INET;
        if (inet_pton(AF_INET, target.toLatin1().constData(), &addr.sin_addr) != 1)
            continue;
        packet.protocol_version = (char *)CHIAKI_DISCOVERY_PROTOCOL_VERSION_PS5;
        addr.sin_port = htons(CHIAKI_DISCOVERY_PORT_PS5);
        chiaki_discovery_send(&m_discovery, &packet, (struct sockaddr *)&addr, sizeof(addr));
        packet.protocol_version = (char *)CHIAKI_DISCOVERY_PROTOCOL_VERSION_PS4;
        addr.sin_port = htons(CHIAKI_DISCOVERY_PORT_PS4);
        chiaki_discovery_send(&m_discovery, &packet, (struct sockaddr *)&addr, sizeof(addr));
    }

    QTimer::singleShot(timeoutMs, this, &ChiakiDiscoveryService::finishDiscovery);
    return true;
}

void ChiakiDiscoveryService::finishDiscovery()
{
    if (!m_running)
        return;
    stop();
    qCInfo(lcDisco) << "discovery finished," << m_consoles.size() << "console(s)";
    emit finished(int(m_consoles.size()));
}

void ChiakiDiscoveryService::stop()
{
    if (!m_running)
        return;
    chiaki_discovery_thread_stop(&m_thread);
    chiaki_discovery_fini(&m_discovery);
    m_running = false;
    emit runningChanged();
}

bool ChiakiDiscoveryService::wakeup(const QString &host, const QString &registKey)
{
    // The wakeup credential is the plaintext regist key read as hex.
    const quint64 credential = registKey.toULongLong(nullptr, 16);
    const ChiakiErrorCode err = chiaki_discovery_wakeup(
        &m_log, nullptr, host.toLatin1().constData(), credential, /*ps5=*/true);
    if (err != CHIAKI_ERR_SUCCESS) {
        qCWarning(lcDisco) << "wakeup failed:" << chiaki_error_string(err);
        emit errorOccurred(QStringLiteral("wakeup failed: %1")
                               .arg(QString::fromUtf8(chiaki_error_string(err))));
        return false;
    }
    qCInfo(lcDisco) << "wakeup sent to" << host;
    return true;
}
