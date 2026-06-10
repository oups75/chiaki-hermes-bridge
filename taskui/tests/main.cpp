#include <QtQuickTest/quicktest.h>
#include <QCoreApplication>
#include <QObject>
#include <QQmlEngine>

// Harness for the taskui Quick Test suite. Names the application so the QML
// Settings instances in Main.qml write to a test-scoped config instead of the
// user's real chiaki-taskui settings.
class Setup : public QObject
{
    Q_OBJECT
public:
    Setup()
    {
        QCoreApplication::setOrganizationName(QStringLiteral("SolowayTest"));
        QCoreApplication::setApplicationName(QStringLiteral("chiaki-taskui-qmltests"));
    }

public slots:
    void qmlEngineAvailable(QQmlEngine *engine)
    {
        Q_UNUSED(engine); // modules resolve via linked plugins (:/qt/qml)
    }
};

QUICK_TEST_MAIN_WITH_SETUP(qmltests, Setup)
#include "main.moc"
