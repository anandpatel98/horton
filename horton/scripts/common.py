# -*- coding: utf-8 -*-
# Horton is a development platform for electronic structure methods.
# Copyright (C) 2011-2013 Toon Verstraelen <Toon.Verstraelen@UGent.be>
#
# This file is part of Horton.
#
# Horton is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 3
# of the License, or (at your option) any later version.
#
# Horton is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, see <http://www.gnu.org/licenses/>
#
#--
'''Code shared by several scripts'''


import os, sys, datetime, numpy as np, h5py as h5, time, contextlib

from horton import UniformGrid, angstrom, periodic, Cell, log, dump_hdf5_low


__all__ = [
    'iter_elements', 'reduce_ugrid', 'reduce_data',
    'parse_h5', 'parse_ewald_args', 'parse_pbc', 'store_args',
    'safe_open_h5', 'write_part_output', 'write_script_output',
]


def iter_elements(elements_str):
    '''Interpret a string as a list of elements

       elements_str
            A string with comma-separated element numbers. One may add ranges
            with the format 'N-M' where M>N.
    '''
    for item in elements_str.split(','):
        if '-' in item:
            words = item.split("-")
            if len(words) != 2:
                raise ValueError("Each item should contain at most one dash.")
            first = periodic[words[0]].number
            last = periodic[words[1]].number
            if first > last:
                raise ValueError('first=%i > last=%i' % (first, last))
            for number in xrange(first,last+1):
                yield number
        else:
            yield periodic[item].number


def reduce_ugrid(ugrid, stride, chop):
    '''Reduce the uniform grid

       **Arguments:**

       ugrid
            The uniform integration grid.

       stride
            The reduction factor.

       chop
            The number of slices to chop of the grid in each direction.

       Returns: a reduced ugrid object
    '''
    if (chop < 0):
        raise ValueError('Chop must be positive or zero.')
    if ((ugrid.shape - chop) % stride != 0).any():
        raise ValueError('The stride is not commensurate with all three grid demsions.')

    new_shape = (ugrid.shape-chop)/stride
    grid_rvecs = ugrid.grid_rvecs*stride
    new_ugrid = UniformGrid(ugrid.origin, grid_rvecs, new_shape, ugrid.pbc)
    return new_ugrid


def reduce_data(cube_data, ugrid, stride, chop):
    '''Reduce the uniform grid data according to stride and chop arguments

       **Arguments:**

       fn_cube
            The cube file with the electron density.

       ugrid
            The uniform integration grid.

       stride
            The reduction factor.

       chop
            The number of slices to chop of the grid in each direction.

       Returns: a new array and an updated grid object
    '''
    new_ugrid = reduce_ugrid(ugrid, stride, chop)

    if chop == 0:
        new_cube_data = cube_data[::stride, ::stride, ::stride].copy()
    else:
        new_cube_data = cube_data[:-chop:stride, :-chop:stride, :-chop:stride].copy()

    return new_cube_data, new_ugrid


def parse_h5(arg_h5, name, path_optional=True):
    '''Parse an HDF5 command line argument of the form file.h5:group or file.h5:dataset

       **Arguments**:

       arg_h5
            The command line argument that consists of two parts, the HDF5
            filename and a path, separated by a colon. If the path is optional,
            the default value is the root group.

       name
            The name of the argument to be used in error messages.

       **Optional arguments:**

       path_optional
            Set this to False when a path in the HDF5 is not optional

       **Returns:** the filename and the group
    '''
    if path_optional:
        if arg_h5.count(':') == 1:
            return arg_h5.split(':')
        elif arg_h5.count(':') == 0:
            return arg_h5, '/'
        else:
            raise ValueError('The hdf5 argument "%s" must contain at most one colon.' % name)
    else:
        if arg_h5.count(':') == 1:
            return arg_h5.split(':')
        else:
            raise ValueError('The hdf5 argument "%s" must contain one colon.' % name)


def check_output(fn_h5, grp_name, overwrite):
    '''Check if the output is already present in print a warning if --overwrite is not used

       **Arguments:**

       fn_h5
            The output HDF5 file

       grp_name
            The HDF5 group in which the output is stored.

       overwrite
            Whether the user wants to overwrite contents that were already
            present.
    '''
    if os.path.isfile(fn_h5):
        with safe_open_h5(fn_h5, 'r') as f:
            if grp_name in f and len(f[grp_name] > 0):
                if overwrite:
                    if log.do_warning:
                        log.warn('Overwriting the contents of "%s" in the file "%s".' % (grp_name, fn_h5))
                    return False
                else:
                    if log.do_warning:
                        log.warn('Skipping because the group "%s" is already present in the file "%s" and it is not empty.' % (grp_name, fn_h5))
                    return True
    return False


def parse_ewald_args(args):
    rcut = args.rcut*angstrom
    alpha = args.alpha_scale/rcut
    gcut = args.gcut_scale*alpha
    return rcut, alpha, gcut


def parse_pbc(spbc):
    if len(spbc) != 3:
        raise ValueError('The pbc argument must consist of three characters, 0 or 1.')
    result = np.zeros(3, int)
    for i in xrange(3):
        if spbc[i] not in '01':
            raise ValueError('The pbc argument must consist of three characters, 0 or 1.')
        result[i] = int(spbc[i])
    return result


def store_args(args, grp):
    '''Convert the command line arguments to hdf5 attributes'''
    grp.attrs['cmdline'] =  ' '.join(sys.argv)
    grp.attrs['pwd'] = os.getcwd()
    grp.attrs['datetime'] = datetime.datetime.now().isoformat()
    for key, val in vars(args).iteritems():
        if val is not None:
            grp.attrs['arg_%s' % key] = val


@contextlib.contextmanager
def safe_open_h5(*args, **kwargs):
    '''Try to open a file and 10 times wait a random time up to seconds between each attempt.

       **Arguments:** the same as those of the h5py.File constructor.

       **Optional keyword arguments:**

       wait
            The maximum number of seconds to wait between two attempts to open
            the file. [default=10]

       count
            The number of attempts to open the file.
    '''
    wait = kwargs.pop('wait', 10.0)
    counter = kwargs.pop('count', 10)
    while True:
        try:
            f = h5.File(*args, **kwargs)
            break
        except:
            counter -= 1
            if counter <= 0:
                raise
            time.sleep(np.random.uniform(0, wait))
            continue
    try:
        yield f
    finally:
        f.close()


def write_part_output(fn_h5, grp_name, part, names, args):
    '''Write the output of horton-wpart.py or horton-cpart.py

       **Arguments:**

       fn_h5
            The filename for the HDF5 output file.

       grp_name
            the destination group

       part
            The partitioning object (instance of subclass of
            horton.part.base.Part)

       names
            The names of the cached items that must go in the HDF5 outut file.

       args
            The results of the command line parser. All arguments are stored
            as attributes in the HDF5 output file.
    '''
    # Store the results in an HDF5 file
    with safe_open_h5(fn_h5) as f:
        # Store results
        if grp_name in f:
            grp = f['grp_name']
            for key in grp.keys():
                del f[key]
        else:
            grp = f.create_group(grp_name)
        for name in names:
            dump_hdf5_low(grp, name, part[name])

        # Store command line arguments
        store_args(args, grp)

        if args.debug:
            # Store additional data for debugging
            if 'debug' in grp:
                del grp['debug']
            grp_debug = grp.create_group('debug')
            for debug_key in part.cache.iterkeys():
                debug_name = '_'.join(str(x) for x in debug_key)
                if debug_name not in names:
                    grp_debug[debug_name] = part.cache.load(*debug_key)

        if log.do_medium:
            log('Results written to %s:%s' % (fn_h5, grp_name))


def write_script_output(fn_h5, grp_name, results, args):
    '''Store the output of a script in an open HDF5 file

       **Arguments:**

       fn_h5
            The HDF5 filename

       grp_name
            The name of the group where the results will be stored.

       results
            A dictionary with results.

       args
            The results of the command line parser. All arguments are stored
            as attributes in the HDF5 output file.
    '''
    with safe_open_h5(fn_h5) as f:
        # Store results
        if grp_name in f:
            grp = f[grp_name]
            for key in grp.keys():
                del f[key]
        else:
            grp = f.require_group(grp_name)
        for key, value in results.iteritems():
            dump_hdf5_low(grp, key, value)

        # Store command-line arguments arguments
        store_args(args, grp)

        if log.do_medium:
            log('Results written to %s:%s' % (fn_h5, grp_name))
