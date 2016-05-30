r"""
Generate interpreters for fast_callable

AUTHORS:

- Carl Witty

This file is part of the Sage support for "planned" computations;
that is, computations that are separated into a planning stage and
a plan-execution stage.  Here, we generate fast interpreters for plan
executions.

There are at least two kinds of computations that are often planned in
this fashion.  First is arithmetic expression evaluation, where we
take an arbitrary user-specified arithmetic expression and compile it
into a bytecode form for fast interpretation.  Second is things like
FFTs and large multiplications, where large problems are split into
multiple smaller problems... we can do the logical "splitting" for a
given size only once, producing a plan which can be reused as often as
we want for different problems of the same size.  Currently only
arithmetic expression evaluation is implemented, but other kinds of
planned computations should be easy to add.

Typically, for arithmetic expressions, we want the storage of
intermediate results to be handled automatically (on a stack); for
FFTs/multiplications/etc., the planner will keep track of intermediate
results itself.

For arithmetic expression evaluation, we want to have lots of
interpreters (at least one, and possibly several, per
specially-handled type).  Also, for any given type, we have many
possible variants of instruction encoding, etc.; some of these could
be handled with conditional compilation, but some are more
complicated.  So we end up writing an interpreter generator.

We want to share as much code as possible across all of these
interpreters, while still maintaining the freedom to make drastic
changes in the interpretation strategy (which may change the
generated code, the calling convention for the interpreter, etc.)

To make this work, the interpreter back-end is divided into three
parts:

1. The interpreter itself, in C or C++.

2. The wrapper, which is a Cython object holding the
   constants, code, etc., and which actually calls the interpreter.

3. The code generator.

We generate parts 1 and 2.  The code generator is table-driven,
and we generate the tables for the code generator.

There are a lot of techniques for fast interpreters that we do not yet
use; hopefully at least some of these will eventually be implemented:

- using gcc's "labels as values" extension where available

- top-of-stack caching

- superinstructions and/or superoperators

- static stack caching

- context threading/subrouting threading

- selective inlining/dynamic superinstructions

- automatic replication

Interpreters may be stack-based or register-based.  Recent research
suggests that register-based interpreters are better, but the
researchers are investigating interpreters for entire programming
languages, rather than interpreters for expressions.  I suspect
that stack-based expression interpreters may be better.  However,
we'll implement both varieties and see what's best.

The relative costs of stack- and register-based interpreters will
depend on the costs of moving values.  For complicated types (like
mpz_t), a register-based interpreter will quite likely be better,
since it will avoid moving values.

We will NOT support any sort of storage of bytecode; instead, the
code must be re-generated from expression trees in every Sage run.
This means that we can trivially experiment with different styles of
interpreter, or even use quite different interpreters depending on
the architecture, without having to worry about forward and backward
compatibility.
"""

#*****************************************************************************
#       Copyright (C) 2009 Carl Witty <Carl.Witty@gmail.com>
#       Copyright (C) 2015 Jeroen Demeyer <jdemeyer@cage.ugent.be>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.
#                  http://www.gnu.org/licenses/
#*****************************************************************************

#####################################################################
# This module is used during the Sage build process, so it should not
# use any other Sage modules.  (In particular, it MUST NOT use any
# Cython modules -- they won't be built yet!)
# Also, we have some trivial dependency tracking, where we don't
# rebuild the interpreters if this file hasn't changed; if
# interpreter configuration is split out into a separate file,
# that will have to be changed.
#####################################################################

from __future__ import print_function

import os

from .generator import InterpreterGenerator, AUTOGEN_WARN
from .instructions import *
from .memory import *
from .specs.cdf import *
from .specs.element import *
from .specs.python import *
from .specs.rdf import *
from .specs.rr import *
from .storage import *
from .utils import *


def build_interp(interp_spec, dir):
    r"""
    Given an InterpreterSpec, write the C interpreter and the Cython
    wrapper (generate a pyx and a pxd file).

    EXAMPLES::

        sage: from sage_setup.autogen.interpreters import *
        sage: testdir = tmp_dir()
        sage: rdf_interp = RDFInterpreter()
        sage: build_interp(rdf_interp, testdir)
        sage: open(testdir + '/interp_rdf.c').readline()
        '/* Automatically generated by ... */\n'
    """
    ig = InterpreterGenerator(interp_spec)
    interp_fn = '%s/interp_%s.c' % (dir, interp_spec.name)
    header_fn = '%s/interp_%s.h' % (dir, interp_spec.name)
    wrapper_fn = '%s/wrapper_%s.pyx' % (dir, interp_spec.name)
    pxd_fn = '%s/wrapper_%s.pxd' % (dir, interp_spec.name)
    interp = ig.get_interpreter()
    wrapper = ig.get_wrapper()
    pxd = ig.get_pxd()
    write_if_changed(interp_fn, interp)
    write_if_changed(wrapper_fn, wrapper)
    write_if_changed(pxd_fn, pxd)


def rebuild(dirname):
    r"""
    Check whether the interpreter and wrapper sources have been written
    since the last time this module was changed.  If not, write them.

    EXAMPLES::

        sage: from sage_setup.autogen.interpreters import *
        sage: testdir = tmp_dir()
        sage: rebuild(testdir)
        Building interpreters for fast_callable
        sage: open(testdir + '/wrapper_el.pyx').readline()
        '# Automatically generated by ...\n'
    """
    # This line will show up in "sage -b" (once per upgrade, not every time
    # you run it).
    print("Building interpreters for fast_callable")

    try:
        os.makedirs(dirname)
    except OSError:
        pass

    interp = RDFInterpreter()
    build_interp(interp, dirname)

    interp = CDFInterpreter()
    build_interp(interp, dirname)

    interp = RRInterpreter()
    build_interp(interp, dirname)

    interp = PythonInterpreter()
    build_interp(interp, dirname)

    interp = ElementInterpreter()
    build_interp(interp, dirname)

    with open(os.path.join(dirname, '__init__.py'), 'w') as f:
        f.write("# " + AUTOGEN_WARN)