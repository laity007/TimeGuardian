"""Simplified Chinese defaults for the application and Qt standard controls."""
from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator
from PySide6.QtGui import QFont


def configure_chinese(app):
    QLocale.setDefault(QLocale("zh_CN"))
    translator = QTranslator(app)
    translations = QLibraryInfo.path(QLibraryInfo.TranslationsPath)
    if translator.load("qtbase_zh_CN", translations):
        app.installTranslator(translator)
    app.chinese_translator = translator
    app.setFont(QFont("Microsoft YaHei UI", 9))
