import sys
import _pytest
import pytest
import IPython
import types
from collections import OrderedDict, namedtuple, defaultdict

def pytest_addoption(parser):
    parser.addoption("--i", "--interactive", action="store_true",
            dest='interactive',
            help="enable iteractive selection of tests after collection")


def pytest_keyboard_interrupt(excinfo):
    'enter the debugger on keyboard interrupt'
    pytest.set_trace()



def pytest_collection_modifyitems(session, config, items):
    """called after collection has been performed, may filter or re-order
    the items in-place."""
    if not (config.option.interactive and items):
        return

    # prep and embed ipython
    from IPython.terminal.embed import InteractiveShellEmbed
    class PytestShellEmbed(InteractiveShellEmbed):
        def exit(self):
            """Handle interactive exit.
            This method calls the ask_exit callback."""
            if getattr(self, 'test_items', None):
                # TODO: maybe list the tests first then count and ask?
                msg = "{}\nYou have selected the above {} test(s) to be run."\
                      "\nWould you like to run these tests now? ([y]/n)?"\
                      .format(self.test_items, len(self.test_items))
            else:
                msg = 'Do you really want to exit ([y]/n)?'
            if self.ask_yes_no(msg,'y'):
                self.ask_exit()

    ipshell = PytestShellEmbed(banner1='Entering IPython workspace...',
                                  exit_msg='Exiting IPython...')

    # build a tree of test items
    tt = TestTree(items, ipshell)
    ipshell("Welcome to pytest-interactive, the pytest + ipython sensation.\n"
            "Please explore the test (collection) tree using tt.<tab>\n"
            "When finshed tabbing to a test node, simply call it to have "
            "pytest invoke all tests selected under that node.")
    items[:] = tt.selection[:]

    # don't run any tests by default
    if not tt._selected and not config.option.collectonly:
        items[:] = []


_root_id = '.'
Package = namedtuple('Package', 'name path node parent')


class ParametrizedFunc(object):
    def __init__(self, name, instances, parent):
        self._instances = OrderedDict()
        self.parent = parent
        if not isinstance(instances, list):
            instances = [instances]
        for item in instances:
            self.append(item)

    def append(self, item):
        self._instances[item._genid] = item

    def __dir__(self):
        attrs = sorted(set(dir(type(self)) + list(self.__dict__.keys())))
        return self._instances + attrs


def gen_path(item, cache):
    '''generate all parent objs of this node up to the root/session'''
    path = ()
    chain = item.listchain()  # lists branch items in order
    for node in chain:
        try:
            name = node._obj.__name__
        except AttributeError as ae:  # when either Instance or non-packaged module
            if isinstance(node, _pytest.python.Instance):
                name = 'Instance'  # instances should be named as such
            elif node.nodeid is _root_id:
                name = _root_id
            else:  # should never get here
                raise ae
        # packaged module
        if '.' in name and isinstance(node, _pytest.python.Module):
            # pkgname = node._obj.__package__
            prefix = tuple(name.split('.'))
            lpath = node.fspath
            # don't include the mod name in path
            for level in prefix[:-1]:
                lpath = lpath.join(level)
                path += (level,)
                yield path, Package(level, lpath, node, node.parent)
            name = prefix[-1]  # this mod's name
        # func item
        elif isinstance(node, _pytest.python.Function):
            # print("function name is {}".format(name))
            name = node.name
            if '[' in name:
                funcname = name.split('[')[0]
                try:
                    # TODO: look up the pf based on the vanilla func obj
                    # (should be an attr on the _pyfuncitem...)
                    pf = cache[path + (funcname,)]
                    pf.append(node)
                except KeyError:
                    pf = ParametrizedFunc(name, node, node.parent)
                path += (funcname,)
                yield path, pf
        path += (name,)
        yield path, node


class TestTree(object):
    def __init__(self, funcitems, ipshell):
        self._shell = ipshell
        self._funcitems = funcitems  # never modify this
        self.selection = []
        self._path2items = OrderedDict()
        self._path2children = {} #defaultdict(set)
        self._selected = False
        self._nodes = {}
        self._cache = {}
        for item in funcitems:
            for path, node in gen_path(item, self._nodes):
                self._path2items.setdefault(path, set()).add(item)
                if path not in self._nodes:
                    self._nodes[path] = node
                    self._path2children.setdefault(path[:-1], set()).add(path)

        self._root = Node(self, (_root_id,))

    def _get_children(self, path):
        'return all children for the node given by path'
        return self._path2children[path]

    def __getattr__(self, key):
        try:
            object.__getattribute__(self, key)
        except AttributeError as ae:
            try:
                return getattr(self._root, key)
            except AttributeError:
                raise ae

    def __dir__(self, key=None):
        attrs = sorted(set(dir(type(self)) + list(self.__dict__.keys())))
        return dir(self._root) + attrs

    def _runall(self, path):
        # XXX can this selection remain ordered to avoid
        # traversing the list again?...imagined speed gain in my head?
        items = self._path2items[path]
        if not self.selection:
            self.selection = [f for f in self._funcitems if f in items]
        else:
            self.selection.extend([f for f in self._funcitems if f in items])

        if not self._selected:
            self._selected = True
        self._shell.test_items = self.selection
        self._shell.exit()


class Node(object):
    def __init__(self, tree, path):
        self._tree = tree
        self._path = path
        self._len = len(path)

    def __dir__(self):
        if isinstance(self._node, ParametrizedFunc):
            return self._node._instances.keys()
        else:
            return sorted([key[self._len] for key in self._children])

    @property
    def _children(self):
        return self._tree._get_children(self._path)

    def __getattr__(self, attr):
        try:
            object.__getattribute__(self, attr)
        except AttributeError as ae:
            try:
                self._get_node(self._path + (attr,))
                return self._sub(attr)
            except TypeError:
                raise ae
            except KeyError:
                raise AttributeError("sub-node '{}' can not be found".format(attr))

    def _get_node(self, path=None):
        if not path:
            path = self._path
        return self._tree._nodes[path]

    _node = property(_get_node)

    def _get_items(self):
        return self._tree._path2items[self._path]

    _items = property(_get_items)

    def _sub(self, key):
        'return a (new/cached) sub node'
        if key is 'parent':
            path = self._path[:-1]
        else:
            path = self._path + (key,)
        return self._tree._cache.setdefault(path, type(self)(self._tree, path))

    def __call__(self):
        'Run all tests under this node'
        return self._tree._runall(self._path)
