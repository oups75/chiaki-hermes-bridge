#include <QtTest/QtTest>
#include <QTemporaryDir>
#include <QFile>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonArray>

#include "ChiakiTaskBridge.h"
#include "TaskTreeModel.h"
#include "InMemoryTaskStore.h"
#include "TaskEnums.h"

// Headless check of the chiaki task bridge: import tasks.json -> model, edit,
// export back -> tasks.json, and the namespace/slugify helpers.
class TstBridge : public QObject
{
    Q_OBJECT
private:
    QTemporaryDir tmp;

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

        InMemoryTaskStore store;
        TaskTreeModel model;
        model.setStore(&store);

        QCOMPARE(bridge.importJson(&model, QStringLiteral("demo")), 1);
        QCOMPARE(model.rootIds().size(), 1);
        const QString gid = model.rootIds().first();
        QCOMPARE(model.task(gid).title, QStringLiteral("open pack"));
        QCOMPARE(model.children(gid).size(), 2);
        QCOMPARE(model.children(gid).first().payload.value("button").toString(),
                 QStringLiteral("cross"));

        // Imported (learned) tasks are pending until approved.
        QCOMPARE(model.task(gid).payload.value("source").toString(), QStringLiteral("learned"));
        QVERIFY(!model.task(gid).payload.value("approved").toBool());

        // Edit: add a third step (with an expected state) + precondition + approve.
        const QString sid = model.addTask(gid, QStringLiteral("press circle"),
                      int(TaskTree::Type::Manual),
                      QVariantMap{{"button", "circle"}, {"type", "button"},
                                  {"expected_state", QVariantMap{{"screenshot", "/s/e.png"},
                                                                 {"scene", "card revealed"}}}});
        QCOMPARE(model.children(gid).size(), 3);
        QVariantMap gp = model.task(gid).payload;
        gp["start_scene"] = "hut store";
        gp["approved"] = true;
        gp["expected_start"] = QVariantMap{{"screenshot", "/s/start.png"}, {"scene", "hut store"}};
        model.updateTask(gid, {{"payload", gp}});
        Q_UNUSED(sid);

        // Export and re-read.
        QVERIFY(bridge.exportJson(&model, QStringLiteral("demo")));
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
        InMemoryTaskStore store;
        TaskTreeModel model;
        model.setStore(&store);
        bridge.importJson(&model, QStringLiteral("demo2"));
        bridge.importJson(&model, QStringLiteral("demo2")); // again
        QCOMPARE(model.rootIds().size(), 1); // not duplicated
    }

    void mergeAddsOnlyNewKeys()
    {
        writeSample(QStringLiteral("demo3"));
        ChiakiTaskBridge bridge;
        bridge.setLearningRoot(tmp.path());
        InMemoryTaskStore store;
        TaskTreeModel model;
        model.setStore(&store);
        bridge.importJson(&model, QStringLiteral("demo3"));
        QCOMPARE(model.rootIds().size(), 1);

        // Gateway "learns" a new task: rewrite tasks.json with the original + a new one.
        const QString path = tmp.path() + "/demo3/tasks.json";
        QJsonObject root = QJsonDocument::fromJson([&] {
            QFile f(path); f.open(QIODevice::ReadOnly); return f.readAll(); }()).object();
        root.insert("new-task", QJsonObject{{"goal", "new task"}, {"key", "new-task"},
                                            {"steps", QJsonArray{}}});
        { QFile f(path); f.open(QIODevice::WriteOnly | QIODevice::Truncate);
          f.write(QJsonDocument(root).toJson()); }

        QCOMPARE(bridge.mergeJson(&model, QStringLiteral("demo3")), 1); // only the new one
        QCOMPARE(model.rootIds().size(), 2);
        // Merging again adds nothing.
        QCOMPARE(bridge.mergeJson(&model, QStringLiteral("demo3")), 0);
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
};

QTEST_MAIN(TstBridge)
#include "tst_bridge.moc"
