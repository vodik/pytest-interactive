import _pytest
import pytest
import math
import errno
import os
from os.path import expanduser, join
from operator import attrgetter, itemgetter
from collections import OrderedDict, namedtuple


def pytest_addoption(parser):
    parser.addoption("--interactive", "--ia", action="store_true",
                     dest='interactive',
                     help="enable iteractive selection of tests after"
                     " collection")


# def pytest_keyboard_interrupt(excinfo):
#     """enter the debugger on keyboard interrupt
#     """
#     if config.option.capture != 'no':
#         return
#     pytest.set_trace()


@pytest.mark.trylast
def pytest_configure(config):
    """called after command line options have been parsed
    and all plugins and initial conftest files been loaded.
    """
    option = config.option
    if option.capture != 'no' and option.interactive:
        tr = config.pluginmanager.getplugin('terminalreporter')
        tr.write('ERROR: ', red=True)
        tr.write_line("you must specify the -s option to use the interactive"
                      " plugin")
        pytest.exit(1)


def pytest_collection_modifyitems(session, config, items):
    """called after collection has been performed, may filter or re-order
    the items in-place.
    """
    if not (config.option.interactive and items):
        return
    else:
        from .shell import PytestShellEmbed, SelectionMagics

    tr = config.pluginmanager.getplugin('terminalreporter')
    # build a tree of test items
    tr.write_line("building test tree...")
    tt = TestTree(items, tr)

    # prep and embed ipython
    fname = 'shell_history.sqlite'
    confdir = join(expanduser('~'), '.config', 'pytest_interactive')
    try:
        os.makedirs(confdir)
    # except FileExistsError:
    #     pass
    except OSError as e:  # py2 compat
        if e.errno == errno.EEXIST:
            pass
        else:
            raise
    PytestShellEmbed.pytest_hist_file = join(confdir, fname)
    ipshell = PytestShellEmbed(banner1='entering ipython workspace...',
                               exit_msg='exiting shell...')
    ipshell.register_magics(SelectionMagics)

    # test tree needs ref to shell
    tt._shell = ipshell
    # shell needs ref to curr selection
    ipshell.test_items = tt._selection

    # FIXME: can we operate on the cls directly and avoid
    # poluting our namespace with items?
    # set the prompt to track number of selected test items
    pm = ipshell.prompt_manager
    bold_prmpt = '{color.number}' '{tt}' '{color.prompt}'
    pm.in_template = "'{}' selected >>> ".format(bold_prmpt)
    # don't rjustify with preceding 'in' prompt
    pm.justify = False

    msg = """Welcome to pytest-interactive, the pytest + ipython sensation.
Please explore the test (collection) tree using tt.<TAB>
When finshed tabbing to a test node, simply call it to have
pytest invoke all tests selected under that node."""

    ipshell(msg, local_ns={
        'tt': tt,
        'shell': ipshell,
        'config': config,
        'session': session,
        })

    # make the final selection
    if tt._selection:
        items[:] = list(tt._selection.values())[:]
    else:
        items[:] = []


_root_id = '.'
Package = namedtuple('Package', 'name path node parent')


def gen_nodes(item, cache):
    '''generate all parent objs of this node up to the root/session'''
    path = ()
    # pytest call which lists path items in order
    chain = item.listchain()
    for node in chain:
        try:
            name = node._obj.__name__
        except AttributeError as ae:
            # when either Instance or non-packaged module
            if isinstance(node, _pytest.python.Instance):
                # leave out Instances, later versions are going to drop them
                # anyway
                continue
            elif node.nodeid is _root_id:
                name = _root_id
            else:  # XXX should never get here
                raise ae

        # packaged module
        if '.' in name and isinstance(node, _pytest.python.Module):
            # FIXME: this should be cwd dependent!!!
            # (i.e. don't add package objects we're below in the fs)
            prefix = tuple(name.split('.'))
            lpath = node.fspath
            fspath = str(lpath)
            # don't include the mod name in path
            for level in prefix[:-1]:
                name = '{}{}'.format(fspath[:fspath.index(level)], level)
                path += (level,)
                yield path, Package(name, lpath, node, node.parent)
            name = prefix[-1]  # this mod's name

        # func item
        elif isinstance(node, _pytest.python.Function):
            name = node.name
            if '[' in name:
                funcname = name.split('[')[0]
                try:
                    # TODO: look up the pf based on the vanilla func obj
                    # (should be an attr on the _pyfuncitem...)
                    pf = cache[path + (funcname,)]
                except KeyError:
                    # parametrized func is a collection of funcs
                    pf = FuncCollection()
                    pf.parent = node.parent  # set parent like other nodes
                pf.append(node)
                path += (funcname,)
                yield path, pf

        # all other nodes
        path += (name,)
        yield path, node


def dirinfo(obj):
    """return relevant __dir__ info for obj
    """
    return sorted(set(dir(type(obj)) + list(obj.__dict__.keys())))


def tosymbol(ident):
    """Replace illegal python characters with underscores
    in the provided string identifier
    """
    ident = str(ident)
    for char in (" ", "-", "/"):
        ident = ident.replace(char, "_")
    if ident[0].isdigit():
        raise TypeError("Can't convert int")
    return ident


class FuncCollection(object):
    '''A selection of functions
    '''
    def __init__(self, funcitems=None):
        self.funcs = OrderedDict()
        if funcitems:
            if not isinstance(funcitems, list):
                funcitems = [funcitems]
            for item in funcitems:
                self.append(item)

    def append(self, item, attr_path='nodeid'):
        self.funcs[tosymbol(attrgetter(attr_path)(item))] = item

    def addtests(self, test_set):
        for item in test_set._items:
            self.append(item)

    def remove(self, test_set):
        for item in test_set._items:
            self.funcs.pop(item.nodeid, None)

    def clear(self):
        self.funcs.clear()

    def keys(self):
        return self.funcs.keys()

    def values(self):
        return self.funcs.values()

    def __len__(self):
        return len(self.funcs)

    def __getitem__(self, key):
        if isinstance(key, int):
            return self.enumitems()[key][1]
        if isinstance(key, (int, slice)):
            return list(map(itemgetter(1), self.enumitems()[key]))
        return self.funcs[key]

    def __dir__(self):
        return list(self.funcs.keys()) + dirinfo(self)

    def items(self):
        return self.funcs.items()

    def enumitems(self, items=None):
        if not items:
            items = self.funcs.values()
        return [(i, node) for i, node in enumerate(items)]


class TestTree(object):
    '''A tree of all collected tests
    '''
    def __init__(self, funcitems, termrep):
        self._funcitems = funcitems  # never modify this
        self._selection = FuncCollection()  # items must be unique
        self._path2items = OrderedDict()
        self._path2children = {}
        self._path2cs = {}  # callspec property to items
        self._nodes = {}
        self._cache = {}
        for item in funcitems:
            for path, node in gen_nodes(item, self._nodes):
                self._path2items.setdefault(path, []).append(item)
                if path not in self._nodes:
                    self._nodes[path] = node
                    # map parent path to set of children paths
                    self._path2children.setdefault(path[:-1], set()).add(path)
        # top level test set
        self._root = TestSet(self, (_root_id,))
        self.__class__.__getitem__ = self._root.__getitem__
        # pytest terminal reporter
        self._tr = termrep

    def __str__(self):
        '''stringify current selection length'''
        return str(len(self._selection))

    def __getattr__(self, key):
        try:
            object.__getattribute__(self, key)
        except AttributeError as ae:
            try:
                return getattr(self._root, key)
            except AttributeError:
                raise ae

    def __dir__(self, key=None):
        return dir(self._root) + dirinfo(self) + dirinfo(self._root)

    def __repr__(self):
        return repr(self._root)

    def _runall(self, path=None):
        """Run all currently selected tests
        """
        self._shell.exit()

    def _tprint(self, items, tr=None):
        '''extended from
        pytest.terminal.TerminalReporter._printcollecteditems
        '''
        if not tr:
            tr = self._tr
        if not items:
            tr.write('ERROR: ', red=True)
            tr.write_line("not enough items to display")
            return
        stack = []
        indent = ""
        ncols = int(math.ceil(math.log10(len(items))))
        for i, item in enumerate(items):
            needed_collectors = item.listchain()[1:]  # strip root node
            while stack:
                if stack == needed_collectors[:len(stack)]:
                    break
                stack.pop()
            for col in needed_collectors[len(stack):]:
                if col.name == "()":
                    continue
                stack.append(col)
                indent = (len(stack) - 1) * "  "
                if col == item:
                    index = "{}".format(i)
                else:
                    index = ''
                indent = indent[:-len(index) or None] + (ncols+1) * " "
                tr.write("{}".format(index), green=True)
                tr.write_line("{}{}".format(indent, col))


class TestSet(object):
    '''Represent a pytest node/item tests set.
    Use as a tab complete-able object in ipython.
    An internal reference is kept to the pertaining pytest Node.
    Hierarchical lookups are delegated to the containing TestTree.
    '''
    def __init__(self, tree, path, indices=None, cs_params=()):
        self._tree = tree
        self._path = path
        self._len = len(path)
        if indices is None:
            indices = slice(indices)
        elif isinstance(indices, int):
            # create a slice which will slice out a single element
            # (the or expr is here for the indices = -1 case)
            indices = slice(indices, indices + 1 or None)
        self._ind = indices
        self._csparams = cs_params
        self._cs_keys = self._get_cs_keys()

    def __repr__(self):
        clsname = self._node.__class__.__name__
        nodename = getattr(self._node, 'name', None)
        return "<{} '{}' -> {} tests>".format(str(clsname), nodename,
                                              len(self._items))

    def __dir__(self):
        # sorted list of child keys
        childkeys = sorted([key[self._len] for key in self._children])
        childkeys.extend(self._cs_keys)
        return childkeys

    def _get_cs_keys(self):
        keys = []
        for item in self._items:
            cs = getattr(item, 'callspec', ())
            if cs:
                # gather parameterized values
                for val in cs.params.values():
                    try:
                        ident = tosymbol(val)
                    except TypeError:
                        continue
                    else:
                        keys.append(ident)
        return [key for key in keys if key not in self._csparams]

    @property
    def _children(self):
        child_paths = self._tree._path2children[self._path]
        if not self._csparams:
            return child_paths
        else:
            # if we have callspec ids in our getattr chain,
            # we need to filter out any children who's items
            # are not included in our set
            paths = []
            for path in child_paths:
                # check intersection of our items with child items
                if set(self._tree._path2items[path]) & set(self._items):
                    paths.append(path)
            return paths

    @property
    def _items(self):
        funcitems = self._tree._path2items[self._path][self._ind]
        if not self._csparams:
            return funcitems
        else:
            items = []
            for item in funcitems:
                try:
                    cs = getattr(item, 'callspec')
                except AttributeError:
                    continue
                else:
                    for val in cs.params.values():
                        if val in self._csparams:
                            items.append(item)
            return items

    def _enumitems(self):
        return self._tree._selection.enumitems(self._items)

    def __getitem__(self, key):
        return self._sub(key)

    def __call__(self, key=None):
        """Select and run all tests under this node
        plus any already selected previously
        """
        self._tree._selection.addtests(self)
        return self._tree._runall()

    def __getattr__(self, attr):
        try:
            object.__getattribute__(self, attr)
        except AttributeError as ae:
            try:
                return self._sub(attr)
            except TypeError:
                raise ae
            except KeyError:
                raise AttributeError("sub-node '{}' can not be found"
                                     .format(attr))

    def _get_node(self, path=None):
        if not path:
            path = self._path
        return self._tree._nodes[path]

    _node = property(_get_node)

    def _sub(self, key):
        '''Return a new subset/node
        '''
        ind = None
        cs = ()
        if isinstance(key, str):
            if key in self._cs_keys:
                # callspec param values/idents
                cs += (key,)
                path = self._path
            elif key is 'parent':
                path = self._path[:-1]
            else:
                path = self._path + (key,)
            # ensure sub-node with name exists
            self._get_node(path)
        elif isinstance(key, (int, slice)):
            path = self._path
            ind = key
        # return a new test set corresponding to 'key'
        ckey = (path, str(key))
        return self._tree._cache.setdefault(
            ckey, type(self)(
                self._tree, path, indices=ind, cs_params=self._csparams + cs
            )
        )
