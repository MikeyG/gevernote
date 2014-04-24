# -*- coding:utf-8 -*-
import sys
sys.path.append('../..')
from PySide.QtGui import (
    QMainWindow, QIcon, QApplication,
    QMessageBox, QMenu, QInputDialog,
    QStandardItemModel, QStandardItem,
    QItemSelection, QKeySequence, QShortcut,
)
from PySide.QtCore import Slot, Qt, QPoint
from everpad.interface.list import Ui_List
from everpad.pad.tools import get_icon
from everpad.basetypes import Notebook, Note, Tag, NONE_ID
import dbus
import datetime


SELECT_NONE = -1


class List(QMainWindow):
    """All Notes dialog"""

    def __init__(self, *args, **kwargs):
        QMainWindow.__init__(self, *args, **kwargs)
        self.app = QApplication.instance()
        self.closed = False
        self.sort_order = None
        self._init_interface()
        self.app.data_changed.connect(self._reload_data)
        self._init_notebooks()
        self._init_tags()
        self._init_notes()

    def _init_interface(self):
        self.ui = Ui_List()
        self.ui.setupUi(self)
        self.setWindowIcon(get_icon())
        self.setWindowTitle(self.tr("Everpad / All Notes"))
        self.ui.newNotebookBtn.setIcon(QIcon.fromTheme('folder-new'))
        self.ui.newNotebookBtn.clicked.connect(self.new_notebook)

        self.ui.newNoteBtn.setIcon(QIcon.fromTheme('document-new'))
        self.ui.newNoteBtn.clicked.connect(self.new_note)

        self.ui.newNoteBtn.setShortcut(QKeySequence(self.tr('Ctrl+n')))
        self.ui.newNotebookBtn.setShortcut(QKeySequence(self.tr('Ctrl+Shift+n')))
        QShortcut(QKeySequence(self.tr('Ctrl+q')), self, self.close)

    def _init_notebooks(self):
        self._current_notebook = None
        self.notebooksModel = QStandardItemModel()
        self.ui.notebooksList.setModel(self.notebooksModel)
        self.ui.notebooksList.selection.connect(self.selection_changed)
        self.ui.notebooksList.setContextMenuPolicy(Qt.CustomContextMenu)
        self.ui.notebooksList.customContextMenuRequested.connect(self.notebook_context_menu)

    def _init_tags(self):
        self._current_tag = None
        self.tagsModel = QStandardItemModel()
        self.ui.tagsList.setModel(self.tagsModel)
        self.ui.tagsList.selection.connect(self.tag_selection_changed)
        self.ui.tagsList.setContextMenuPolicy(Qt.CustomContextMenu)
        self.ui.tagsList.customContextMenuRequested.connect(self.tag_context_menu)

    def _init_notes(self):
        self._current_note = None
        self.notesModel = QStandardItemModel()
        self.notesModel.setHorizontalHeaderLabels(
            [self.tr('Title'), self.tr('Last Updated')])

        self.ui.notesList.setModel(self.notesModel)
        self.ui.notesList.selection.connect(self.note_selection_changed)
        self.ui.notesList.doubleClicked.connect(self.note_dblclicked)
        self.ui.notesList.setContextMenuPolicy(Qt.CustomContextMenu)
        self.ui.notesList.customContextMenuRequested.connect(self.note_context_menu)
        self.ui.notesList.header().sortIndicatorChanged.connect(self.sort_order_updated)

    @Slot(QItemSelection, QItemSelection)
    def selection_changed(self, selected, deselected):
        if len(selected.indexes()):
            self.ui.tagsList.clearSelection()
            self.notebook_selected(selected.indexes()[-1])

    @Slot(QItemSelection, QItemSelection)
    def tag_selection_changed(self, selected, deselected):
        if len(selected.indexes()):
            self.ui.notebooksList.clearSelection()
            self.tag_selected(selected.indexes()[-1])

    @Slot(QItemSelection, QItemSelection)
    def note_selection_changed(self, selected, deselected):
        if len(selected.indexes()):
            self.note_selected(selected.indexes()[-1])

    def showEvent(self, *args, **kwargs):
        super(List, self).showEvent(*args, **kwargs)
        self._reload_data()
        self.readSettings()

    def writeSettings(self):
        self.app.settings.setValue('list-geometry', self.saveGeometry())
        for key, widget in self._getRestorableItems():
            self.app.settings.setValue(key, widget.saveState())

    def _getRestorableItems(self):
        return (
            ('list-splitter-state', self.ui.splitter),
            ('list-header-state', self.ui.notesList.header()),
        )

    def readSettings(self):
        geometry = self.app.settings.value('list-geometry')
        if geometry:
            self.restoreGeometry(geometry)

        for key, widget in self._getRestorableItems():
            state = self.app.settings.value(key)
            if state:
                widget.restoreState(state)

    def closeEvent(self, event):
        self.writeSettings()
        event.ignore()
        self.closed = True
        self.hide()

    @Slot(int, Qt.SortOrder)
    def sort_order_updated(self, logicalIndex, order):
        self.sort_order = (logicalIndex, order.name)
        self.app.settings.setValue('list-notes-sort-order', self.sort_order)

    def note_selected(self, index):
        self._current_note = index

    def notebook_selected(self, index):
        self.notesModel.setRowCount(0)

        item = self.notebooksModel.itemFromIndex(index)
        if hasattr(item, 'notebook'):
            notebook_id = item.notebook.id
        else:
            notebook_id = 0

        self._current_notebook = notebook_id
        self._current_tag = SELECT_NONE

        notebook_filter = [notebook_id] if notebook_id > 0 else dbus.Array([], signature='i')

        if hasattr(item, 'stack'):  # stack selected, retrieve all underlying notebooks
            notebook_filter = []
            for notebook_struct in self.app.provider.list_notebooks():
                notebook = Notebook.from_tuple(notebook_struct)
                if(notebook.stack == item.stack):
                    notebook_filter.append(notebook.id)

        notes = self.app.provider.find_notes(
            '', notebook_filter, dbus.Array([], signature='i'),
            0, 2 ** 31 - 1, Note.ORDER_TITLE, -1,
        )  # fails with sys.maxint in 64

        for note_struct in notes:
            note = Note.from_tuple(note_struct)
            self.notesModel.appendRow(QNoteItemFactory(note).make_items())

        sort_order = self.sort_order
        if sort_order is None:
            sort_order = self.app.settings.value('list-notes-sort-order')

        if sort_order:
            logicalIndex, order = sort_order
            order = Qt.SortOrder.values[order]
            self.ui.notesList.sortByColumn(int(logicalIndex), order)

    def tag_selected(self, index):
        self.notesModel.setRowCount(0)

        item = self.tagsModel.itemFromIndex(index)
        if hasattr(item, 'tag'):
            tag_id = item.tag.id
        else:
            tag_id = 0

        self._current_notebook = SELECT_NONE
        self._current_tag = tag_id

        tag_filter = [tag_id] if tag_id > 0 else dbus.Array([], signature='i')
        notes = self.app.provider.find_notes(
            '', dbus.Array([], signature='i'), tag_filter,
            0, 2 ** 31 - 1, Note.ORDER_TITLE, -1,
        )  # fails with sys.maxint in 64
        for note_struct in notes:
            note = Note.from_tuple(note_struct)
            self.notesModel.appendRow(QNoteItemFactory(note).make_items())

        sort_order = self.sort_order
        if sort_order is None:
            sort_order = self.app.settings.value('list-notes-sort-order')

        if sort_order:
            logicalIndex, order = sort_order
            order = Qt.SortOrder.values[order]
            self.ui.notesList.sortByColumn(int(logicalIndex), order)

    @Slot()
    def note_dblclicked(self, index):
        item = self.notesModel.itemFromIndex(index)
        self.app.indicator.open(item.note)

    @Slot()
    def new_notebook(self, oldStack=''):
        name, status, stack = self._notebook_new_name(self.tr('Create new notebook'), '', oldStack)
        if status:
            notebook_struct = self.app.provider.create_notebook(name, stack)
            notebook = Notebook.from_tuple(notebook_struct)

            self.app.send_notify(self.tr('Notebook "%s" created!') % notebook.name)
            self._reload_notebooks_list(notebook.id)

    @Slot()
    def rename_notebook(self):
        index = self.ui.notebooksList.currentIndex()
        item = self.notebooksModel.itemFromIndex(index)
        notebook = item.notebook
        name, status, stack = self._notebook_new_name(
            self.tr('Rename notebook'), notebook.name, notebook.stack
        )
        if status:
            notebook.name = name
            notebook.stack = stack
            self.app.provider.update_notebook(notebook.struct)
            self.app.send_notify(self.tr('Notebook "%s" renamed!') % notebook.name)
            self._reload_notebooks_list(notebook.id)

    @Slot()
    def remove_notebook(self):
        msg = QMessageBox(
            QMessageBox.Critical,
            self.tr("You are trying to delete a notebook"),
            self.tr("Are you sure want to delete this notebook and its notes?"),
            QMessageBox.Yes | QMessageBox.No
        )
        if msg.exec_() == QMessageBox.Yes:
            index = self.ui.notebooksList.currentIndex()
            item = self.notebooksModel.itemFromIndex(index)
            self.app.provider.delete_notebook(item.notebook.id)
            self.app.send_notify(self.tr('Notebook "%s" deleted!') % item.notebook.name)
            self._reload_notebooks_list()

    @Slot()
    def rename_stack(self):
        index = self.ui.notebooksList.currentIndex()
        item = self.notebooksModel.itemFromIndex(index)
        stack = item.stack
        name, status = self._stack_new_name(
            self.tr('Rename stack'), stack,
        )
        if status:
            # loop notebooks and update stack str
            for notebook_struct in self.app.provider.list_notebooks():
                notebook = Notebook.from_tuple(notebook_struct)
                if(notebook.stack == item.stack):
                    notebook.stack = name
                    self.app.provider.update_notebook(notebook.struct)

            self.app.send_notify(self.tr('Stack "%s" renamed!') % name)
            self._reload_notebooks_list(notebook.id)

    @Slot()
    def remove_stack(self):
        msg = QMessageBox(
            QMessageBox.Critical,
            self.tr("You are trying to delete a stack"),
            self.tr("Are you sure want to delete this stack? Notebooks and notes are preserved."),
            QMessageBox.Yes | QMessageBox.No
        )
        if msg.exec_() == QMessageBox.Yes:
            index = self.ui.notebooksList.currentIndex()
            item = self.notebooksModel.itemFromIndex(index)
            # loop notebooks and remove stack str
            for notebook_struct in self.app.provider.list_notebooks():
                notebook = Notebook.from_tuple(notebook_struct)
                if(notebook.stack == item.stack):
                    print "Clearing one notebook from its stack."
                    notebook.stack = ''
                    self.app.provider.update_notebook(notebook.struct)

            self._reload_notebooks_list()

    @Slot()
    def remove_tag(self):
        msg = QMessageBox(
            QMessageBox.Critical,
            self.tr("You are trying to delete a tag"),
            self.tr("Are you sure want to delete this tag and untag all notes tagged with it?"),
            QMessageBox.Yes | QMessageBox.No
        )
        if msg.exec_() == QMessageBox.Yes:
            index = self.ui.tagsList.currentIndex()
            item = self.tagsModel.itemFromIndex(index)
            self.app.provider.delete_tag(item.tag.id)
            self.app.send_notify(self.tr('Tag "%s" deleted!') % item.tag.name)
            self._reload_tags_list()

    @Slot()
    def new_note(self):
        index = self.ui.notebooksList.currentIndex()
        notebook_id = NONE_ID
        if index.row():
            item = self.notebooksModel.itemFromIndex(index)
            notebook_id = item.notebook.id

        self.app.indicator.create(notebook_id=notebook_id)

    @Slot()
    def edit_note(self):
        index = self.ui.notesList.currentIndex()
        item = self.notesModel.itemFromIndex(index)
        self.app.indicator.open(item.note)

    @Slot()
    def remove_note(self):
        index = self.ui.notesList.currentIndex()
        item = self.notesModel.itemFromIndex(index)
        msgBox = QMessageBox(
            QMessageBox.Critical,
            self.tr("You are trying to delete a note"),
            self.tr('Are you sure want to delete note "%s"?') % item.note.title,
            QMessageBox.Yes | QMessageBox.No
        )
        if msgBox.exec_() == QMessageBox.Yes:
            self.app.provider.delete_note(item.note.id)
            self.app.send_notify(self.tr('Note "%s" deleted!') % item.note.title)
            self.notebook_selected(self.ui.notebooksList.currentIndex())

    @Slot(QPoint)
    def notebook_context_menu(self, pos):
        index = self.ui.notebooksList.currentIndex()
        item = self.notebooksModel.itemFromIndex(index)
        if hasattr(item, 'notebook'):
            menu = QMenu(self.ui.notebooksList)
            menu.addAction(QIcon.fromTheme('gtk-edit'), self.tr('Rename'), self.rename_notebook)
            menu.addAction(QIcon.fromTheme('gtk-delete'), self.tr('Remove'), self.remove_notebook)
            menu.exec_(self.ui.notebooksList.mapToGlobal(pos))
        if hasattr(item, 'stack'):
            menu = QMenu(self.ui.notebooksList)
            menu.addAction(QIcon.fromTheme('gtk-edit'), self.tr('Rename'), self.rename_stack)
            menu.addAction(QIcon.fromTheme('gtk-delete'), self.tr('Remove'), self.remove_stack)
            menu.exec_(self.ui.notebooksList.mapToGlobal(pos))

    @Slot(QPoint)
    def tag_context_menu(self, pos):
        index = self.ui.tagsList.currentIndex()
        item = self.tagsModel.itemFromIndex(index)
        if hasattr(item, 'tag'):
            menu = QMenu(self.ui.tagsList)
            menu.addAction(QIcon.fromTheme('gtk-delete'), self.tr('Remove'), self.remove_tag)
            menu.exec_(self.ui.tagsList.mapToGlobal(pos))

    @Slot(QPoint)
    def note_context_menu(self, pos):
        menu = QMenu(self.ui.notesList)
        menu.addAction(QIcon.fromTheme('gtk-edit'), self.tr('Edit'), self.edit_note)
        menu.addAction(QIcon.fromTheme('gtk-delete'), self.tr('Remove'), self.remove_note)
        menu.exec_(self.ui.notesList.mapToGlobal(pos))

    def _reload_data(self):
        self._reload_notebooks_list(self._current_notebook)
        self._reload_tags_list(self._current_tag)
        self._mark_note_selected(self._current_note)

    def _reload_notebooks_list(self, select_notebook_id=None):
        # TODO could enable selecting an already selected stack
        self.notebooksModel.clear()
        root = QStandardItem(QIcon.fromTheme('user-home'), self.tr('All Notes'))
        self.notebooksModel.appendRow(root)
        selected_item = root

        stacks = {}
        for notebook_struct in self.app.provider.list_notebooks():
            notebook = Notebook.from_tuple(notebook_struct)
            count = self.app.provider.get_notebook_notes_count(notebook.id)
            item = QNotebookItem(notebook, count)

            if(notebook.stack == ''):
                root.appendRow(item)
            else:
                if(notebook.stack not in stacks.keys()):
                    stack = QStandardItem(QIcon.fromTheme('user-home'), notebook.stack)
                    stack.stack = notebook.stack
                    root.appendRow(stack)
                    stacks[notebook.stack] = stack

                stacks[notebook.stack].appendRow(item)

            if select_notebook_id and notebook.id == select_notebook_id:
                selected_item = item

        self.ui.notebooksList.expandAll()

        if selected_item and not select_notebook_id == SELECT_NONE:
            index = self.notebooksModel.indexFromItem(selected_item)
            self.ui.notebooksList.setCurrentIndex(index)
            self.notebook_selected(index)

    def _notebook_new_name(self, title, exclude='', oldStack=''):
        names = map(lambda nb: Notebook.from_tuple(nb).name, self.app.provider.list_notebooks())
        try:
            names.remove(exclude)
        except ValueError:
            pass
        name, status = QInputDialog.getText(self, title, self.tr('Enter notebook name:'), text=exclude)
        while name in names and status:
            message = self.tr('Notebook with this name already exist. Enter notebook name')
            name, status = QInputDialog.getText(self, title, message)
        if status:
            stack, status = QInputDialog.getText(self, title, self.tr('Enter stack name (empty for no stack):'), text=oldStack)
        else:
            stack = oldStack
        return name, status, stack

    def _stack_new_name(self, title, value=''):
        name, status = QInputDialog.getText(self, title, self.tr('Enter stack name:'), text=value)
        return name, status

    def _reload_tags_list(self, select_tag_id=None):
        # TODO nested tags
        self.tagsModel.clear()
        tagRoot = QStandardItem(QIcon.fromTheme('user-home'), self.tr('All Tags'))
        self.tagsModel.appendRow(tagRoot)
        selected_item = tagRoot

        for tag_struct in self.app.provider.list_tags():
            tag = Tag.from_tuple(tag_struct)
            count = self.app.provider.get_tag_notes_count(tag.id)
            item = QTagItem(tag, count)
            tagRoot.appendRow(item)

            if select_tag_id and tag.id == select_tag_id:
                selected_item = item

        self.ui.tagsList.expandAll()
        if selected_item and not select_tag_id == SELECT_NONE:
            index = self.tagsModel.indexFromItem(selected_item)
            self.ui.tagsList.setCurrentIndex(index)
            self.tag_selected(index)

    def _mark_note_selected(self, index):
        if index:
            self.ui.notesList.setCurrentIndex(index)


class QNotebookItem(QStandardItem):
    def __init__(self, notebook, count):
        super(QNotebookItem, self).__init__(QIcon.fromTheme('folder'), '%s (%d)' % (notebook.name, count))
        self.notebook = notebook


class QTagItem(QStandardItem):
    def __init__(self, tag, count):
        super(QTagItem, self).__init__(QIcon.fromTheme('folder'), '%s (%d)' % (tag.name, count))
        self.tag = tag


class QNoteItemFactory(object):
    def __init__(self, note):
        self.note = note

    def make_items(self):
        items = [QStandardItem(unicode(self.note.title)),
                 QStandardItem(unicode(datetime.datetime.fromtimestamp(
                     self.note.updated / 1000.0)))]
        for item in items:
            item.note = self.note
        items[0].setIcon(QIcon.fromTheme('x-office-document'))
        items[1].setEditable(False)

        return items
