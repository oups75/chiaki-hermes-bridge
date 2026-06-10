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

        // Edit: add a third step + set a precondition scene + approve.
        model.addTask(gid, QStringLiteral("press circle"), int(TaskTree::Type::Manual),
                      QVariantMap{{"button", "circle"}, {"type", "button"}});
        QCOMPARE(model.children(gid).size(), 3);
        QVariantMap gp = model.task(gid).payload;
        gp["start_scene"] = "hut store";
        gp["approved"] = true;
        model.updateTask(gid, {{"payload", gp}});

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
};

QTEST_MAIN(TstBridge)
#include "tst_bridge.moc"
