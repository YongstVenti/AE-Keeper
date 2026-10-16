from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from browser_pane import BrowserPane


class WindowContextMenuTests(unittest.TestCase):
    def test_right_click_window_or_child_restores_that_specific_window(self):
        for clicked in ('window:Edge:abc','tab:Edge:one'):
            tree = Mock(); tree.identify_row.return_value = clicked; tree.parent.return_value = 'window:Edge:abc'
            callback, menu = Mock(), Mock()
            pane = SimpleNamespace(browser='Edge',tree=tree,window_ids={'window:Edge:abc':'abc'},window_menu=menu,on_restore_window=callback)
            BrowserPane._context_menu(pane,SimpleNamespace(y=50,x_root=400,y_root=500))
            self.assertEqual(menu.add_command.call_args.kwargs['label'],'恢复这个窗口的全部标签页')
            menu.add_command.call_args.kwargs['command']()
            callback.assert_called_once_with('Edge','abc')
            tree.selection_set.assert_called_once_with('window:Edge:abc')
            menu.grab_release.assert_called_once()

    def test_right_click_empty_space_does_not_offer_restore(self):
        tree = Mock(); tree.identify_row.return_value = ''
        pane = SimpleNamespace(tree=tree,window_menu=Mock())
        BrowserPane._context_menu(pane,SimpleNamespace(y=999))
        pane.window_menu.tk_popup.assert_not_called()
