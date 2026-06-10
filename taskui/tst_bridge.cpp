#include <QtTest/QtTest>
#include <QTemporaryDir>
#include <QFile>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonArray>

#include "ChiakiTaskBridge.h"
#include "ChiakiDiscoveryService.h"
#include "ChiakiProcess.h"
#include "TaskTreeModel.h"
#include "TaskTreeHost.h"
#include "RemoteTaskClient.h"
#include "InMemoryTaskStore.h"
#include "TaskEnums.h"

#include <QtTaskTree/qtasktree.h>

// Headless check of the chiaki task bridge against the QtRO task service:
// host (store+model) and client share the process over a local socket; the
// bridge mutates through the client (RPC), assertions read the host model.
class TstBridge : public QObject
{
    Q_OBJECT
private:
    QTemporaryDir tmp;
    int m_rigSeq = 0;

    struct Rig {
        InMemoryTaskStore store;
        TaskTreeModel model;
        TaskTreeHost host;
        RemoteTaskClient client;
    };

    std::unique_ptr<Rig> makeRig()
    {
        auto rig = std::make_unique<Rig>();
        rig->model.setStore(&rig->store);
        const QString url = QStringLiteral("local:tstbridge_%1_%2")
                                .arg(QCoreApplication::applicationPid()).arg(++m_rigSeq);
        rig->host.setUrl(url);
        rig->host.setModel(&rig->model);
        if (!rig->host.start())
            return nullptr;
        rig->client.setUrl(url);
        if (!rig->client.connectToHost())
            return nullptr;
        return rig;
    }

    void writeSample(const QString &ns)
    {
        QDir().mkpath(tmp.path() + "/" + ns);
        const QJsonObject step1{{"name", "press cross"}, {"button", "cross"},
                                {"type", "button"}, {"wait_ms", 200}};
        const QJsonObject step2{{"name", "wait reveal"}, {"button", "none"},
                                {"type", "wait"}, {"wait_ms", 1500}};
        const QJsonObject task{{"goal", "open pack"}, {"key", "open-pack"},
                               {"namespace", ns},
                               {"steps", QJsonArray{step1, step2}}};
        QFile f(tmp.path() + "/" + ns + "/tasks.json");
        QVERIFY(f.open(QIODevice::WriteOnly));
        f.write(QJsonDocument(QJsonObject{{"open-pack", task}}).toJson());
    }

    // Deterministic discovery: reports one ready console, records wakeups.
    class FakeDiscovery : public ChiakiDiscoveryService
    {
    public:
        using ChiakiDiscoveryService::ChiakiDiscoveryService;
        QString wokenHost;
        bool discover(int) override
        {
            setConsolesForTest({QVariantMap{
                {"host", "127.0.0.1"}, {"state", "ready"}, {"name", "PS5-fake"}}});
            QMetaObject::invokeMethod(this, [this] { emit finished(1); },
                                      Qt::QueuedConnection);
            return true;
        }
        bool wakeup(const QString &host, const QString &) override
        {
            wokenHost = host;
            return true;
        }
    };

private slots:
    void slugifyMatchesGateway()
    {
        QCOMPARE(ChiakiTaskBridge::slugify("Open Pack!"), QStringLiteral("open-pack"));
        QCOMPARE(ChiakiTaskBridge::slugify("NHL26  HUT  Auction"), QStringLiteral("nhl26-hut-auction"));
    }

    void importEditExportRoundTrip()
    {
        writeSample(QStringLiteral("demo"));

        ChiakiTaskBridge bridge;
        bridge.setLearningRoot(tmp.path());
        QVERIFY(bridge.namespaces().contains(QStringLiteral("demo")));

        auto rig = makeRig();
        QVERIFY(rig);
        QTRY_VERIFY_WITH_TIMEOUT(rig->client.connected(), 5000);

        QCOMPARE(bridge.importJson(&rig->client, QStringLiteral("demo")), 1);
        // Group add is blocking, step adds are async — host model settles first.
        QTRY_COMPARE_WITH_TIMEOUT(rig->model.rootIds().size(), 1, 5000);
        const QString gid = rig->model.rootIds().first();
        QCOMPARE(rig->model.task(gid).title, QStringLiteral("open pack"));
        QTRY_COMPARE_WITH_TIMEOUT(rig->model.children(gid).size(), 2, 5000);
        QCOMPARE(rig->model.children(gid).first().payload.value("button").toString(),
                 QStringLiteral("cross"));

        // Imported (learned) tasks are pending until approved.
        QCOMPARE(rig->model.task(gid).payload.value("source").toString(),
                 QStringLiteral("learned"));
        QVERIFY(!rig->model.task(gid).payload.value("approved").toBool());

        // Edit via the client: a third step + precondition + approve.
        rig->client.addTask(gid, QStringLiteral("press circle"),
                            int(TaskTree::Type::Manual),
                            QVariantMap{{"button", "circle"}, {"type", "button"},
                                        {"expected_state", QVariantMap{{"screenshot", "/s/e.png"},
                                                                       {"scene", "card revealed"}}}});
        QTRY_COMPARE_WITH_TIMEOUT(rig->model.children(gid).size(), 3, 5000);
        QVariantMap gp = rig->model.task(gid).payload;
        gp["start_scene"] = "hut store";
        gp["approved"] = true;
        gp["expected_start"] = QVariantMap{{"screenshot", "/s/start.png"}, {"scene", "hut store"}};
        rig->client.updateTask(gid, {{"payload", gp}});
        QTRY_VERIFY_WITH_TIMEOUT(
            rig->model.task(gid).payload.value("approved").toBool(), 5000);

        // Export reads the replica — wait for it to mirror the edits. Role data
        // is lazy-fetched: poll until the newest child's title+payload arrive.
        QAbstractItemModel *replica = rig->client.model();
        QTRY_COMPARE_WITH_TIMEOUT(replica->rowCount(), 1, 5000);
        QTRY_COMPARE_WITH_TIMEOUT(replica->rowCount(replica->index(0, 0)), 3, 5000);
        QTRY_VERIFY_WITH_TIMEOUT(
            replica->data(replica->index(0, 0), TaskTreeModel::PayloadRole)
                .toMap().value("approved").toBool(), 5000);
        QTRY_COMPARE_WITH_TIMEOUT(
            replica->data(replica->index(2, 0, replica->index(0, 0)),
                          TaskTreeModel::TitleRole).toString(),
            QStringLiteral("press circle"), 5000);
        QTRY_VERIFY_WITH_TIMEOUT(
            !replica->data(replica->index(2, 0, replica->index(0, 0)),
                           TaskTreeModel::PayloadRole).toMap().isEmpty(), 5000);

        QVERIFY(bridge.exportJson(&rig->client, QStringLiteral("demo")));
        QFile f(tmp.path() + "/demo/tasks.json");
        QVERIFY(f.open(QIODevice::ReadOnly));
        const QJsonObject root = QJsonDocument::fromJson(f.readAll()).object();
        QVERIFY(root.contains(QStringLiteral("open-pack")));
        const QJsonObject task = root.value(QStringLiteral("open-pack")).toObject();
        QCOMPARE(task.value("goal").toString(), QStringLiteral("open pack"));
        QCOMPARE(task.value("steps").toArray().size(), 3);
        QCOMPARE(task.value("steps").toArray().last().toObject().value("name").toString(),
                 QStringLiteral("press circle"));
        // Precondition + approval persisted.
        QCOMPARE(task.value("start_scene").toString(), QStringLiteral("hut store"));
        QCOMPARE(task.value("source").toString(), QStringLiteral("learned"));
        QVERIFY(task.value("approved").toBool());
        // Expected states persisted (task-level + step-level).
        QCOMPARE(task.value("expected_start").toObject().value("scene").toString(),
                 QStringLiteral("hut store"));
        QCOMPARE(task.value("steps").toArray().last().toObject()
                     .value("expected_state").toObject().value("scene").toString(),
                 QStringLiteral("card revealed"));
    }

    void reimportReplacesContent()
    {
        writeSample(QStringLiteral("demo2"));
        ChiakiTaskBridge bridge;
        bridge.setLearningRoot(tmp.path());
        auto rig = makeRig();
        QVERIFY(rig);
        QTRY_VERIFY_WITH_TIMEOUT(rig->client.connected(), 5000);

        bridge.importJson(&rig->client, QStringLiteral("demo2"));
        QTRY_COMPARE_WITH_TIMEOUT(rig->model.rootIds().size(), 1, 5000);
        // Replica must mirror the first import (including the lazily-fetched
        // id role) before re-importing: the bridge collects the ids to replace
        // from the replica.
        QTRY_COMPARE_WITH_TIMEOUT(rig->client.rootIds(), rig->model.rootIds(), 5000);

        bridge.importJson(&rig->client, QStringLiteral("demo2")); // again
        QTRY_COMPARE_WITH_TIMEOUT(rig->model.rootIds().size(), 1, 5000); // not duplicated
    }

    void mergeAddsOnlyNewKeys()
    {
        writeSample(QStringLiteral("demo3"));
        ChiakiTaskBridge bridge;
        bridge.setLearningRoot(tmp.path());
        auto rig = makeRig();
        QVERIFY(rig);
        QTRY_VERIFY_WITH_TIMEOUT(rig->client.connected(), 5000);

        bridge.importJson(&rig->client, QStringLiteral("demo3"));
        // Wait for the replica to carry the imported task's key (lazy payload).
        QTRY_COMPARE_WITH_TIMEOUT(rig->model.rootIds().size(), 1, 5000);
        QTRY_COMPARE_WITH_TIMEOUT(
            rig->client.taskInfo(rig->model.rootIds().first())
                .value("payload").toMap().value("key").toString(),
            QStringLiteral("open-pack"), 5000);

        // Gateway "learns" a new task: rewrite tasks.json with the original + a new one.
        const QString path = tmp.path() + "/demo3/tasks.json";
        QJsonObject root = QJsonDocument::fromJson([&] {
            QFile f(path); f.open(QIODevice::ReadOnly); return f.readAll(); }()).object();
        root.insert("new-task", QJsonObject{{"goal", "new task"}, {"key", "new-task"},
                                            {"steps", QJsonArray{}}});
        { QFile f(path); f.open(QIODevice::WriteOnly | QIODevice::Truncate);
          f.write(QJsonDocument(root).toJson()); }

        QCOMPARE(bridge.mergeJson(&rig->client, QStringLiteral("demo3")), 1); // only the new one
        QTRY_COMPARE_WITH_TIMEOUT(rig->model.rootIds().size(), 2, 5000);
        // Merging again adds nothing (replica payloads synced first).
        QTRY_VERIFY_WITH_TIMEOUT(
            [&] {
                const QStringList ids = rig->client.rootIds();
                if (ids.size() != 2)
                    return false;
                for (const QString &id : ids)
                    if (rig->client.taskInfo(id).value("payload").toMap()
                            .value("key").toString().isEmpty())
                        return false;
                return true;
            }(), 5000);
        QCOMPARE(bridge.mergeJson(&rig->client, QStringLiteral("demo3")), 0);
    }

    void chiakiProcessConfig()
    {
        // Fake chiaki root so program/env wiring is observable.
        const QString root = tmp.path() + "/chiakiroot";
        QDir().mkpath(root + "/bin");

        ChiakiProcess p;
        p.setChiakiRoot(root);
        p.setupStream(QStringLiteral("PS5-test"), QStringLiteral("10.0.0.7"),
                      {QStringLiteral("--fullscreen")});

        QCOMPARE(p.program(), root + QStringLiteral("/bin/chiaki"));
        QCOMPARE(p.arguments(),
                 (QStringList{"stream", "PS5-test", "10.0.0.7", "--fullscreen"}));
        const QProcessEnvironment env = p.processEnvironment();
        QVERIFY(env.value("LD_LIBRARY_PATH").startsWith(root + "/lib"));
        QCOMPARE(env.value("QT_PLUGIN_PATH"), root + "/plugins");
        QCOMPARE(env.value("QT_WEBENGINE_RESOURCES_PATH"), root + "/resources");
        // Unix child setup applied (signal reset, fd hygiene, own session).
        const auto flags = p.unixProcessParameters().flags;
        QVERIFY(flags & QProcess::UnixProcessFlag::ResetSignalHandlers);
        QVERIFY(flags & QProcess::UnixProcessFlag::CloseFileDescriptors);
        QVERIFY(flags & QProcess::UnixProcessFlag::CreateNewSession);
        QVERIFY(bool(p.childProcessModifier()));

        p.setupGui();
        QVERIFY(p.arguments().isEmpty());
    }

    void chiakiProcessTaskRuns()
    {
        // Stand-in chiaki binary: succeed iff invoked as `chiaki stream N H`.
        const QString root = tmp.path() + "/chiakiroot2";
        QDir().mkpath(root + "/bin");
        const QString bin = root + "/bin/chiaki";
        { QFile f(bin); QVERIFY(f.open(QIODevice::WriteOnly));
          f.write("#!/bin/sh\n[ \"$1\" = stream ] && [ -n \"$2\" ] && [ -n \"$3\" ]\n");
          f.setPermissions(f.permissions() | QFileDevice::ExeOwner); }

        using namespace QtTaskTree;
        QTaskTree tree;
        tree.setRecipe(Group{ChiakiProcessTask(
            [root](ChiakiProcess &p) {
                p.setChiakiRoot(root);
                p.setupStream(QStringLiteral("PS5-test"), QStringLiteral("10.0.0.7"));
            })});
        QSignalSpy done(&tree, &QTaskTree::done);
        tree.start();
        QVERIFY(done.wait(5000));
        QCOMPARE(done.first().first().value<DoneWith>(), DoneWith::Success);
    }

    void startSessionStreamsWhenReady()
    {
        // Fake discovery reports a ready PS5; fake chiaki binary accepts the
        // stream invocation. Exercises discover -> launchSession (no wakeup).
        const QString root = tmp.path() + "/chiakiroot3";
        QDir().mkpath(root + "/bin");
        { QFile f(root + "/bin/chiaki"); QVERIFY(f.open(QIODevice::WriteOnly));
          f.write("#!/bin/sh\nsleep 0.4\n[ \"$1\" = stream ]\n");
          f.setPermissions(f.permissions() | QFileDevice::ExeOwner); }

        ChiakiTaskBridge bridge;
        bridge.setChiakiRoot(root);
        bridge.setDiscoveryService(new FakeDiscovery);

        // Needs a registered console in ~/.config/Chiaki/Chiaki.conf for the
        // nickname; skip on machines without one.
        QSignalSpy err(&bridge, &ChiakiTaskBridge::errorOccurred);
        QSignalSpy out(&bridge, &ChiakiTaskBridge::runOutput);
        QSignalSpy running(&bridge, &ChiakiTaskBridge::chiakiRunningChanged);
        bridge.startSession(QStringLiteral("demo"));
        if (!err.isEmpty()
            && err.first().first().toString().contains(QStringLiteral("no registered console")))
            QSKIP("no registered console in Chiaki.conf");

        QVERIFY(out.wait(5000)); // "PS5 127.0.0.1 ready — starting stream"
        QVERIFY(out.first().first().toString().contains(QStringLiteral("ready")));
        QTRY_VERIFY_WITH_TIMEOUT(bridge.chiakiRunning(), 5000); // stream task up
        QTRY_VERIFY_WITH_TIMEOUT(!bridge.chiakiRunning(), 5000); // fake exits
        Q_UNUSED(running);
    }

    void discoveryServiceProbesQuietLan()
    {
        // Real sockets, no console expected on the test LAN: the probe must
        // come back cleanly (count >= 0) without hanging or erroring.
        ChiakiDiscoveryService disco;
        QSignalSpy done(&disco, &ChiakiDiscoveryService::finished);
        QSignalSpy errors(&disco, &ChiakiDiscoveryService::errorOccurred);
        QVERIFY(disco.discover(800));
        QVERIFY(disco.running());
        QVERIFY(done.wait(5000));
        QVERIFY(!disco.running());
        QCOMPARE(errors.count(), 0);
        QCOMPARE(done.first().first().toInt(), disco.consoles().size());
    }

    void discoveryServiceWakeupSends()
    {
        // UDP wakeup to loopback: sendto succeeds without any console.
        ChiakiDiscoveryService disco;
        QVERIFY(disco.wakeup(QStringLiteral("127.0.0.1"), QStringLiteral("776ad678")));
    }

    void classifyParsesPage()
    {
        // Fake gateway that prints a classify result.
        const QString fake = tmp.path() + "/fake_gateway.py";
        { QFile f(fake); QVERIFY(f.open(QIODevice::WriteOnly));
          f.write("print('{\"page\": \"hut store\"}')\n"); }

        ChiakiTaskBridge bridge;
        bridge.setGatewayScript(fake);
        QSignalSpy spy(&bridge, &ChiakiTaskBridge::contextChanged);
        bridge.classify(QStringLiteral("demo"));
        QVERIFY(spy.wait(5000));
        QCOMPARE(spy.first().first().toString(), QStringLiteral("hut store"));
    }

    void captureExpectedReturnsSceneAndPath()
    {
        // Fake gateway: print a page for `classify`, succeed silently otherwise.
        const QString fake = tmp.path() + "/fake_gw2.py";
        { QFile f(fake); QVERIFY(f.open(QIODevice::WriteOnly));
          f.write("import sys\n"
                  "if 'classify' in sys.argv:\n"
                  "    print('{\"page\": \"hut store\"}')\n"); }

        ChiakiTaskBridge bridge;
        bridge.setGatewayScript(fake);
        bridge.setLearningRoot(tmp.path());
        QSignalSpy spy(&bridge, &ChiakiTaskBridge::expectedCaptured);
        bridge.captureExpected(QStringLiteral("demo"));
        QVERIFY(spy.wait(5000));
        QCOMPARE(spy.first().at(1).toString(), QStringLiteral("hut store"));
        QVERIFY(spy.first().at(0).toString().contains(QStringLiteral("/screenshots/demo/expected-")));
    }
};

QTEST_MAIN(TstBridge)
#include "tst_bridge.moc"
