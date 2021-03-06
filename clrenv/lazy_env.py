from __future__ import print_function
from builtins import object
import os.path
from os import environ
from glob import glob
from itertools import chain, groupby
import shlex
import sys
import types

from munch import Munch, munchify
import yaml

from .path import find_environment_path, find_user_environment_paths
from functools import reduce


class LazyEnv(object):
    def __init__(self):
        self.__mode = tuple(shlex.split(environ.get('CLRENV_MODE', '')))
        self.__env = None

    def is_set(self):
        return self.__env is not None

    def set_mode(self, *mode):
        assert not self.is_set()
        self.__mode = mode

    def get_mode(self):
        return self.__mode

    def __getattr__(self, key):
        if self.__env is None:
            self.__env = get_env(*self.__mode)

        try:
            return getattr(self.__env, key)
        except AttributeError:
            return None

    def __getitem__(self, key):
        return self.__getattr__(key)

_env = {}
def get_env(*mode):
    global _env

    if not mode in _env:
        y = (_load_current_environment(),)
        upaths = find_user_environment_paths()
        y = tuple(yaml.load(open(p).read()) for p in upaths if os.path.isfile(p)) + y

        assignments = tuple(m for m in mode if m.find('=') != -1)
        mode = tuple(m for m in mode if m.find('=') == -1)

        overrides = []
        for a in assignments:
            overrides.append(a.split('=', 1))

        allenvs = set(chain(*(list(x.keys()) for x in y)))
        if len(set(mode) - allenvs) != 0:
            raise EnvironmentError('Modes %s not defined anywhere' % (set(mode) - allenvs))

        dicts = reduce(lambda it, m: chain((x.get(m, {}) for x in y), it), mode, [])
        dicts = chain(dicts, (x.get('base', {}) for x in y))

        e = _merged(*dicts)

        for k, v in overrides:
            for pytype in (yaml.load, eval, int, float, str):
                try:
                    pyval = pytype(v)
                    break
                except:
                    pass
            else:
                print('Failed to convert %s into anything sensible!' % v, file=sys.stderr)
                sys.exit(1)

            e = _setattr_rec(e, k, pyval)

        e = munchify(e)
        e = _glob_filenames(e)
        e = _apply_functions(e)
        e = _coerce_none_to_string(e)

        _env[mode] = e


    return _env[mode]

def _coerce_none_to_string(d):
    new = Munch()

    for k, v in list(d.items()):
        if v is None:
            v = ''
        elif isinstance(v, dict):
            v = _coerce_none_to_string(v)

        new[k] = v

    return new

def _glob_filenames(d):
    new = Munch()

    for k, v in list(d.items()):
        if isinstance(v, dict):
            v = _glob_filenames(v)
        elif isinstance(v, str):
            v = os.path.expandvars(v)
            if len(v) > 0 and v[0] in ('~', '/'):
                v = os.path.expanduser(v)
                globbed = glob(v)
                if len(globbed) > 0:
                    v = globbed[0]

        new[k] = v

    return new

def _setattr_rec(d, k, v):
    new = Munch(d)

    if k.find('.') == -1:
        new[k] = v
    else:
        this, rest = k.split('.', 1)
        if hasattr(new, this):
            new[this] = _setattr_rec(new, rest, v)
        else:
            setattr(new, k, v)

    return new

def _load_current_environment():
    with open(find_environment_path()) as f:
        environment = yaml.load(f.read())
    return environment

_kf_dict_cache = {}
def _get_keyfile_cache():
    """
    To avoid loading the encrypted file for each key, cache it.
    Make sure to call _clear_keyfile_cache() once the cache is no longer needed.
    """
    import clrypt
    global _kf_dict_cache
    if not _kf_dict_cache:
        _kf_dict_cache = clrypt.read_file_as_dict('keys', 'keys')
    return _kf_dict_cache

def _clear_keyfile_cache():
    global _kf_dict_cache
    _kf_dict_cache = {}

def _apply_functions(d, recursive=False):
    """Apply a set of functions to the given environment. Functions
    are parsed from values of the format:

      ^function rest

    Currently, the only function available is `keyfile', which attempts
    to replace with a value from the currently loaded keyfile."""
    new = Munch()

    for k, v in list(d.items()):
        if isinstance(v, dict):
            v = _apply_functions(v, recursive=True)
        elif isinstance(v, str):
            if v.startswith('^keyfile '):
                v = v[9:]
                v = _get_keyfile_cache().get(v, '')

        new[k] = v

    if not recursive:
        # Cache no longer needed, clear encrypted data.
        _clear_keyfile_cache()
    return new

def _merge(dst, src):
    """Merges src into dst, overwriting values if necessary."""
    for key in src:
        if key in dst and isinstance(dst[key], dict) and isinstance(src[key], dict):
            _merge(dst[key], src[key])
        else:
            dst[key] = src[key]
    return dst

def _merged(*dicts):
    """Merge dictionaries in *dicts, specified in priority order."""
    return reduce(_merge, reversed(dicts))
