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

"""Implements the generic interpreter generator."""

from __future__ import print_function, absolute_import

from collections import defaultdict

from six.moves import cStringIO as StringIO

from .memory import string_of_addr
from .utils import je, indent_lines, reindent_lines as ri


AUTOGEN_WARN = "Automatically generated by {}.  Do not edit!".format(__file__)


class InterpreterGenerator(object):
    r"""
    This class takes an InterpreterSpec and generates the corresponding
    C interpreter and Cython wrapper.

    See the documentation for methods get_wrapper and get_interpreter
    for more information.
    """

    def __init__(self, spec):
        r"""
        Initialize an InterpreterGenerator.

        INPUT:

        - ``spec`` -- an InterpreterSpec

        EXAMPLES::

            sage: from sage_setup.autogen.interpreters import *
            sage: interp = RDFInterpreter()
            sage: gen = InterpreterGenerator(interp)
            sage: gen._spec is interp
            True
            sage: gen.uses_error_handler
            False
        """
        self._spec = spec
        self.uses_error_handler = False

    def gen_code(self, instr_desc, write):
        r"""
        Generates code for a single instruction.

        INPUTS:
            instr_desc -- an InstrSpec
            write -- a Python callable

        This function calls its write parameter successively with
        strings; when these strings are concatenated, the result is
        the code for the given instruction.

        See the documentation for the get_interpreter method for more
        information.

        EXAMPLES::

            sage: from sage_setup.autogen.interpreters import *
            sage: interp = RDFInterpreter()
            sage: gen = InterpreterGenerator(interp)
            sage: import cStringIO
            sage: buff = cStringIO.StringIO()
            sage: instrs = dict([(ins.name, ins) for ins in interp.instr_descs])
            sage: gen.gen_code(instrs['div'], buff.write)
            sage: print(buff.getvalue())
                case 8: /* div */
                  {
                    double i1 = *--stack;
                    double i0 = *--stack;
                    double o0;
                    o0 = i0 / i1;
                    *stack++ = o0;
                  }
                  break;
            <BLANKLINE>
        """

        d = instr_desc
        w = write
        s = self._spec

        if d.uses_error_handler:
            self.uses_error_handler = True

        w(je(ri(4, """
            case {{ d.opcode }}: /* {{ d.name }} */
              {
        """), d=d))

        # If the inputs to an instruction come from the stack,
        # then we want to generate code for the inputs in reverse order:
        # for instance, the divide instruction, which takes inputs A and B
        # and generates A/B, needs to pop B off the stack first.
        # On the other hand, if the inputs come from the constant pool,
        # then we want to generate code for the inputs in normal order,
        # because the addresses in the code stream will be in that order.
        # We handle this by running through the inputs in two passes:
        # first a forward pass, where we handle non-stack inputs
        # (and lengths for stack inputs), and then a reverse pass,
        # where we handle stack inputs.
        for i in range(len(d.inputs)):
            (ch, addr, input_len) = d.inputs[i]
            chst = ch.storage_type
            if addr is not None:
                w("        int ai%d = %s;\n" % (i, string_of_addr(addr)))
            if input_len is not None:
                w("        int n_i%d = %s;\n" % (i, string_of_addr(input_len)))
            if not ch.is_stack():
                # Shouldn't hardcode 'code' here
                if ch.name == 'code':
                    w("        %s i%d = %s;\n" % (chst.c_local_type(), i, string_of_addr(ch)))
                elif input_len is not None:
                    w("        %s i%d = %s + ai%d;\n" %
                      (chst.c_ptr_type(), i, ch.name, i))
                else:
                    w("        %s i%d = %s[ai%d];\n" %
                      (chst.c_local_type(), i, ch.name, i))

        for i in reversed(range(len(d.inputs))):
            (ch, addr, input_len) = d.inputs[i]
            chst = ch.storage_type
            if ch.is_stack():
                if input_len is not None:
                    w("        %s -= n_i%d;\n" % (ch.name, i))
                    w("        %s i%d = %s;\n" % (chst.c_ptr_type(), i, ch.name))
                else:
                    w("        %s i%d = *--%s;\n" % (chst.c_local_type(), i, ch.name))
                    if ch.is_python_refcounted_stack():
                        w("        *%s = NULL;\n" % ch.name)

        for i in range(len(d.outputs)):
            (ch, addr, output_len) = d.outputs[i]
            chst = ch.storage_type
            if addr is not None:
                w("        int ao%d = %s;\n" % (i, string_of_addr(addr)))
            if output_len is not None:
                w("        int n_o%d = %s;\n" % (i, string_of_addr(output_len)))
                if ch.is_stack():
                    w("        %s o%d = %s;\n" %
                      (chst.c_ptr_type(), i, ch.name))
                    w("        %s += n_o%d;\n" % (ch.name, i))
                else:
                    w("        %s o%d = %s + ao%d;\n" %
                      (chst.c_ptr_type(), i, ch.name, i))
            else:
                if not chst.cheap_copies():
                    if ch.is_stack():
                        w("        %s o%d = *%s++;\n" %
                          (chst.c_local_type(), i, ch.name))
                    else:
                        w("        %s o%d = %s[ao%d];\n" %
                          (chst.c_local_type(), i, ch.name, i))
                else:
                    w("        %s o%d;\n" % (chst.c_local_type(), i))
        w(indent_lines(8, d.code.rstrip('\n') + '\n'))

        stack_offsets = defaultdict(int)
        for i in range(len(d.inputs)):
            (ch, addr, input_len) = d.inputs[i]
            chst = ch.storage_type
            if ch.is_python_refcounted_stack() and not d.handles_own_decref:
                if input_len is None:
                    w("        Py_DECREF(i%d);\n" % i)
                    stack_offsets[ch] += 1
                else:
                    w(je(ri(8, """
                        int {{ iter }};
                        for ({{ iter }} = 0; {{ iter }} < n_i{{ i }}; {{ iter }}++) {
                          Py_CLEAR(i{{ i }}[{{ iter }}]);
                        }
                    """), iter='_interp_iter_%d' % i, i=i))

        for i in range(len(d.outputs)):
            ch = d.outputs[i][0]
            chst = ch.storage_type
            if chst.python_refcounted():
                # We don't yet support code chunks
                # that produce multiple Python values, because of
                # the way it complicates error handling.
                assert i == 0
                w("        if (!CHECK(o%d)) {\n" % i)
                w("          Py_XDECREF(o%d);\n" % i)
                w("          goto error;\n")
                w("        }\n")
                self.uses_error_handler = True
            if chst.cheap_copies():
                if ch.is_stack():
                    w("        *%s++ = o%d;\n" % (ch.name, i))
                else:
                    w("        %s[ao%d] = o%d;\n" % (ch.name, i, i))

        w(je(ri(6,
            """\
                }
                break;
            """)))

    def func_header(self, cython=False):
        r"""
        Generates the function header for the declaration (in the Cython
        wrapper) or the definition (in the C interpreter) of the interpreter
        function.

        EXAMPLES::

            sage: from sage_setup.autogen.interpreters import *
            sage: interp = ElementInterpreter()
            sage: gen = InterpreterGenerator(interp)
            sage: print(gen.func_header())
            PyObject* interp_el(PyObject** args,
                    PyObject** constants,
                    PyObject** stack,
                    PyObject* domain,
                    int* code)
            sage: print(gen.func_header(cython=True))
            object interp_el(PyObject** args,
                    PyObject** constants,
                    PyObject** stack,
                    PyObject* domain,
                    int* code)
        """
        s = self._spec
        ret_ty = 'bint' if cython else 'int'
        if s.return_type:
            ret_ty = s.return_type.c_decl_type()
            if cython:
                ret_ty = s.return_type.cython_decl_type()
        return je(ri(0, """\
            {{ ret_ty }} interp_{{ s.name }}(
            {%- for ch in s.chunks %}
            {%    if not loop.first %},
                    {% endif %}{{ ch.declare_parameter() }}
            {%- endfor %})"""), ret_ty=ret_ty, s=s)

    def write_interpreter(self, write):
        r"""
        Generate the code for the C interpreter.

        This function calls its write parameter successively with
        strings; when these strings are concatenated, the result is
        the code for the interpreter.

        See the documentation for the get_interpreter method for more
        information.

        EXAMPLES::

            sage: from sage_setup.autogen.interpreters import *
            sage: interp = RDFInterpreter()
            sage: gen = InterpreterGenerator(interp)
            sage: import cStringIO
            sage: buff = cStringIO.StringIO()
            sage: gen.write_interpreter(buff.write)
            sage: print(buff.getvalue())
            /* Automatically generated by ...
        """
        s = self._spec
        w = write
        w(je(ri(0, """
            /* {{ warn }} */
            #include <Python.h>
            {% print(s.c_header) %}

            {{ myself.func_header() }} {
              while (1) {
                switch (*code++) {
            """), s=s, myself=self, i=indent_lines, warn=AUTOGEN_WARN))

        for instr_desc in s.instr_descs:
            self.gen_code(instr_desc, w)
        w(je(ri(0, """
                }
              }
            {% if myself.uses_error_handler %}
            error:
              return {{ s.err_return }};
            {% endif %}
            }

            """), s=s, i=indent_lines, myself=self))

    def write_wrapper(self, write):
        r"""
        Generate the code for the Cython wrapper.
        This function calls its write parameter successively with
        strings; when these strings are concatenated, the result is
        the code for the wrapper.

        See the documentation for the get_wrapper method for more
        information.

        EXAMPLES::

            sage: from sage_setup.autogen.interpreters import *
            sage: interp = RDFInterpreter()
            sage: gen = InterpreterGenerator(interp)
            sage: import cStringIO
            sage: buff = cStringIO.StringIO()
            sage: gen.write_wrapper(buff.write)
            sage: print(buff.getvalue())
            # Automatically generated by ...
        """
        s = self._spec
        w = write
        types = set()
        do_cleanup = False
        for ch in s.chunks:
            if ch.storage_type is not None:
                types.add(ch.storage_type)
            do_cleanup = do_cleanup or ch.needs_cleanup_on_error()
        for ch in s.chunks:
            if ch.name == 'args':
                arg_ch = ch

        the_call = je(ri(0, """
                    {% if s.return_type %}return {% endif -%}
            {% if s.adjust_retval %}{{ s.adjust_retval }}({% endif %}
            interp_{{ s.name }}({{ arg_ch.pass_argument() }}
            {% for ch in s.chunks[1:] %}
                        , {{ ch.pass_argument() }}
            {% endfor %}
                        ){% if s.adjust_retval %}){% endif %}

            """), s=s, arg_ch=arg_ch)

        the_call_c = je(ri(0, """
                    {% if s.return_type %}result[0] = {% endif %}
            interp_{{ s.name }}(args
            {% for ch in s.chunks[1:] %}
                        , {{ ch.pass_call_c_argument() }}
            {% endfor %}
                        )

            """), s=s, arg_ch=arg_ch)

        w(je(ri(0, """
            # {{ warn }}
            # distutils: sources = sage/ext/interpreters/interp_{{ s.name }}.c
            {{ s.pyx_header }}

            include "cysignals/memory.pxi"
            from cpython.ref cimport PyObject
            cdef extern from "Python.h":
                void Py_DECREF(PyObject *o)
                void Py_INCREF(PyObject *o)
                void Py_CLEAR(PyObject *o)

                object PyList_New(Py_ssize_t len)
                ctypedef struct PyListObject:
                    PyObject **ob_item

                ctypedef struct PyTupleObject:
                    PyObject **ob_item

            from sage.ext.fast_callable cimport Wrapper

            cdef extern:
                {{ myself.func_header(cython=true) -}}

            {% if s.err_return != 'NULL' %}
             except? {{ s.err_return }}
            {% endif %}

            cdef class Wrapper_{{ s.name }}(Wrapper):
                # attributes are declared in corresponding .pxd file

                def __init__(self, args):
                    Wrapper.__init__(self, args, metadata)
                    cdef int i
                    cdef int count
            {% for ty in types %}
            {% print(indent_lines(8, ty.local_declarations)) %}
            {% print(indent_lines(8, ty.class_member_initializations)) %}
            {% endfor %}
            {% for ch in s.chunks %}
            {% print(ch.init_class_members()) %}
            {% endfor %}
            {% print(indent_lines(8, s.extra_members_initialize)) %}

                def __dealloc__(self):
                    cdef int i
            {% for ch in s.chunks %}
            {% print(ch.dealloc_class_members()) %}
            {% endfor %}

                def __call__(self, *args):
                    if self._n_args != len(args): raise ValueError
            {% for ty in types %}
            {% print(indent_lines(8, ty.local_declarations)) %}
            {% endfor %}
            {% print(indent_lines(8, arg_ch.setup_args())) %}
            {% for ch in s.chunks %}
            {% print(ch.declare_call_locals()) %}
            {% endfor %}
            {% if do_cleanup %}
                    try:
            {% print(indent_lines(4, the_call)) %}
                    except BaseException:
            {%   for ch in s.chunks %}
            {%     if ch.needs_cleanup_on_error() %}
            {%       print(indent_lines(12, ch.handle_cleanup())) %}
            {%     endif %}
            {%   endfor %}
                        raise
            {% else %}
            {% print(the_call) %}
            {% endif %}
            {% if not s.return_type %}
                    return retval
            {% endif %}

            {% if s.implement_call_c %}
                cdef bint call_c(self,
                                 {{ arg_ch.storage_type.c_ptr_type() }} args,
                                 {{ arg_ch.storage_type.c_reference_type() }} result) except 0:
            {% if do_cleanup %}
                    try:
            {% print(indent_lines(4, the_call_c)) %}
                    except BaseException:
            {%   for ch in s.chunks %}
            {%     if ch.needs_cleanup_on_error() %}
            {%       print(indent_lines(12, ch.handle_cleanup())) %}
            {%     endif %}
            {%   endfor %}
                        raise
            {% else %}
            {% print(the_call_c) %}
            {% endif %}
                    return 1
            {% endif %}

            from sage.ext.fast_callable import CompilerInstrSpec, InterpreterMetadata
            metadata = InterpreterMetadata(by_opname={
            {% for instr in s.instr_descs %}
              '{{ instr.name }}':
              (CompilerInstrSpec({{ instr.n_inputs }}, {{ instr.n_outputs }}, {{ instr.parameters }}), {{ instr.opcode }}),
            {% endfor %}
             },
             by_opcode=[
            {% for instr in s.instr_descs %}
              ('{{ instr.name }}',
               CompilerInstrSpec({{ instr.n_inputs }}, {{ instr.n_outputs }}, {{ instr.parameters }})),
            {% endfor %}
             ],
             ipow_range={{ s.ipow_range }})
            """), s=s, myself=self, types=types, arg_ch=arg_ch,
                 indent_lines=indent_lines, the_call=the_call,
                 the_call_c=the_call_c, do_cleanup=do_cleanup,
                 warn=AUTOGEN_WARN))

    def write_pxd(self, write):
        r"""
        Generate the pxd file for the Cython wrapper.
        This function calls its write parameter successively with
        strings; when these strings are concatenated, the result is
        the code for the pxd file.

        See the documentation for the get_pxd method for more
        information.

        EXAMPLES::

            sage: from sage_setup.autogen.interpreters import *
            sage: interp = RDFInterpreter()
            sage: gen = InterpreterGenerator(interp)
            sage: import cStringIO
            sage: buff = cStringIO.StringIO()
            sage: gen.write_pxd(buff.write)
            sage: print(buff.getvalue())
            # Automatically generated by ...
        """
        s = self._spec
        w = write
        types = set()
        for ch in s.chunks:
            if ch.storage_type is not None:
                types.add(ch.storage_type)
        for ch in s.chunks:
            if ch.name == 'args':
                arg_ch = ch

        w(je(ri(0, """
            # {{ warn }}

            from cpython cimport PyObject

            from sage.ext.fast_callable cimport Wrapper
            {% print(s.pxd_header) %}

            cdef class Wrapper_{{ s.name }}(Wrapper):
            {% for ty in types %}
            {% print(indent_lines(4, ty.class_member_declarations)) %}
            {% endfor %}
            {% for ch in s.chunks %}
            {% print(ch.declare_class_members()) %}
            {% endfor %}
            {% print(indent_lines(4, s.extra_class_members)) %}
            {% if s.implement_call_c %}
                cdef bint call_c(self,
                                 {{ arg_ch.storage_type.c_ptr_type() }} args,
                                 {{ arg_ch.storage_type.c_reference_type() }} result) except 0
            {% endif %}
            """), s=s, myself=self, types=types, indent_lines=indent_lines,
                  arg_ch=arg_ch, warn=AUTOGEN_WARN))

    def get_interpreter(self):
        r"""
        Return the code for the C interpreter.

        EXAMPLES:

        First we get the InterpreterSpec for several interpreters::

            sage: from sage_setup.autogen.interpreters import *
            sage: rdf_spec = RDFInterpreter()
            sage: rr_spec = RRInterpreter()
            sage: el_spec = ElementInterpreter()

        Then we get the actual interpreter code::

            sage: rdf_interp = InterpreterGenerator(rdf_spec).get_interpreter()
            sage: rr_interp = InterpreterGenerator(rr_spec).get_interpreter()
            sage: el_interp = InterpreterGenerator(el_spec).get_interpreter()

        Now we can look through these interpreters.

        Each interpreter starts with a file header; this can be
        customized on a per-interpreter basis::

            sage: print(rr_interp)
            /* Automatically generated by ... */
            ...

        Next is the function header, with one argument per memory chunk
        in the interpreter spec::

            sage: print(el_interp)
            /* ... */ ...
            PyObject* interp_el(PyObject** args,
                    PyObject** constants,
                    PyObject** stack,
                    PyObject* domain,
                    int* code) {
            ...

        Currently, the interpreters have a very simple structure; just
        grab the next instruction and execute it, in a switch
        statement::

            sage: print(rdf_interp)
            /* ... */ ...
              while (1) {
                switch (*code++) {
            ...

        Then comes the code for each instruction.  Here is one of the
        simplest instructions::

            sage: print(rdf_interp)
            /* ... */ ...
                case 10: /* neg */
                  {
                    double i0 = *--stack;
                    double o0;
                    o0 = -i0;
                    *stack++ = o0;
                  }
                  break;
            ...

        We simply pull the top of the stack into a variable, negate it,
        and write the result back onto the stack.

        Let's look at the MPFR-based version of this instruction.
        This is an example of an interpreter with an auto-reference
        type::

            sage: print(rr_interp)
            /* ... */ ...
                case 10: /* neg */
                  {
                    mpfr_ptr i0 = *--stack;
                    mpfr_ptr o0 = *stack++;
                    mpfr_neg(o0, i0, MPFR_RNDN);
                  }
                  break;
            ...

        Here we see that the input and output variables are actually
        just pointers into the stack.  But due to the auto-reference
        trick, the actual code snippet, ``mpfr_net(o0, i0, MPFR_RNDN);``,
        is exactly the same as if i0 and o0 were declared as local
        mpfr_t variables.

        For completeness, let's look at this instruction in the
        Python-object element interpreter::

            sage: print(el_interp)
            /* ... */ ...
                case 10: /* neg */
                  {
                    PyObject* i0 = *--stack;
                    *stack = NULL;
                    PyObject* o0;
                    o0 = PyNumber_Negative(i0);
                    Py_DECREF(i0);
                    if (!CHECK(o0)) {
                      Py_XDECREF(o0);
                      goto error;
                    }
                    *stack++ = o0;
                  }
                  break;
            ...

        The original code snippet was only ``o0 = PyNumber_Negative(i0);``;
        all the rest is automatically generated.  For ElementInterpreter,
        the CHECK macro actually checks for an exception (makes sure that
        o0 is not NULL), tests if the o0 is an element with the correct
        parent, and if not converts it into the correct parent.  (That is,
        it can potentially modify the variable o0.)
        """

        buff = StringIO()
        self.write_interpreter(buff.write)
        return buff.getvalue()

    def get_wrapper(self):
        r"""
        Return the code for the Cython wrapper.

        EXAMPLES:

        First we get the InterpreterSpec for several interpreters::

            sage: from sage_setup.autogen.interpreters import *
            sage: rdf_spec = RDFInterpreter()
            sage: rr_spec = RRInterpreter()
            sage: el_spec = ElementInterpreter()

        Then we get the actual wrapper code::

            sage: rdf_wrapper = InterpreterGenerator(rdf_spec).get_wrapper()
            sage: rr_wrapper = InterpreterGenerator(rr_spec).get_wrapper()
            sage: el_wrapper = InterpreterGenerator(el_spec).get_wrapper()

        Now we can look through these wrappers.

        Each wrapper starts with a file header; this can be
        customized on a per-interpreter basis (some blank lines have been
        elided below)::

            sage: print(rdf_wrapper)
            # Automatically generated by ...
            include "cysignals/memory.pxi"
            from cpython.ref cimport PyObject
            cdef extern from "Python.h":
                void Py_DECREF(PyObject *o)
                void Py_INCREF(PyObject *o)
                void Py_CLEAR(PyObject *o)
            <BLANKLINE>
                object PyList_New(Py_ssize_t len)
                ctypedef struct PyListObject:
                    PyObject **ob_item
            <BLANKLINE>
                ctypedef struct PyTupleObject:
                    PyObject **ob_item
            <BLANKLINE>
            from sage.ext.fast_callable cimport Wrapper
            ...

        We need a way to propagate exceptions back to the wrapper,
        even though we only return a double from interp_rdf.  The
        ``except? -1094648009105371`` (that's a randomly chosen
        number) means that we will return that number if there's an
        exception, but the wrapper still has to check whether that's a
        legitimate return or an exception.  (Cython does this
        automatically.)

        Next comes the actual wrapper class.  The member declarations
        are in the corresponding pxd file; see the documentation for
        get_pxd to see them::

            sage: print(rdf_wrapper)
            # ...
            cdef class Wrapper_rdf(Wrapper):
                # attributes are declared in corresponding .pxd file
            ...

        Next is the __init__ method, which starts like this::

            sage: print(rdf_wrapper)
            # ...
                def __init__(self, args):
                    Wrapper.__init__(self, args, metadata)
                    cdef int i
                    cdef int count
            ...

        To make it possible to generate code for all expression
        interpreters with a single code generator, all wrappers
        have the same API.  The __init__ method takes a single
        argument (here called *args*), which is a dictionary holding
        all the information needed to initialize this wrapper.

        We call Wrapper.__init__, which saves a copy of this arguments
        object and of the interpreter metadata in the wrapper.  (This is
        only used for debugging.)

        Now we allocate memory for each memory chunk.  (We allocate
        the memory here, and reuse it on each call of the
        wrapper/interpreter.  This is for speed reasons; in a fast
        interpreter like RDFInterpreter, there are no memory allocations
        involved in a call of the wrapper, except for the ones that
        are required by the Python calling convention.  Eventually
        we will support alternate Cython-only entry points that do
        absolutely no memory allocation.)

        Basically the same code is repeated, with minor variations, for
        each memory chunk; for brevity, we'll only show the code
        for 'constants'::

            sage: print(rdf_wrapper)
            # ...
                    val = args['constants']
                    self._n_constants = len(val)
                    self._constants = <double*>sig_malloc(sizeof(double) * len(val))
                    if self._constants == NULL: raise MemoryError
                    for i in range(len(val)):
                        self._constants[i] = val[i]
            ...

        Recall that _n_constants is an int, and _constants is a
        double*.

        The RRInterpreter version is more complicated, because it has to
        call mpfr_init::

            sage: print(rr_wrapper)
            # ...
                    cdef RealNumber rn
            ...
                    val = args['constants']
                    self._n_constants = len(val)
                    self._constants = <mpfr_t*>sig_malloc(sizeof(mpfr_t) * len(val))
                    if self._constants == NULL: raise MemoryError
                    for i in range(len(val)):
                        mpfr_init2(self._constants[i], self.domain.prec())
                    for i in range(len(val)):
                        rn = self.domain(val[i])
                        mpfr_set(self._constants[i], rn.value, MPFR_RNDN)
            ...

        And as described in the documentation for get_pxd, in
        Python-object based interpreters we actually allocate the
        memory as a Python list::

            sage: print(el_wrapper)
            # ...
                    val = args['constants']
                    self._n_constants = len(val)
                    self._list_constants = PyList_New(self._n_constants)
                    self._constants = (<PyListObject *>self._list_constants).ob_item
                    for i in range(len(val)):
                        self._constants[i] = <PyObject *>val[i]; Py_INCREF(self._constants[i])
            ...

        Of course, once we've allocated the memory, we eventually have
        to free it.  (Again, we'll only look at 'constants'.)::

            sage: print(rdf_wrapper)
            # ...
                def __dealloc__(self):
            ...
                    if self._constants:
                        sig_free(self._constants)
            ...

        The RRInterpreter code is more complicated again because it has
        to call mpfr_clear::

            sage: print(rr_wrapper)
            # ...
                def __dealloc__(self):
                    cdef int i
            ...
                    if self._constants:
                        for i in range(self._n_constants):
                            mpfr_clear(self._constants[i])
                        sig_free(self._constants)
            ...

        But the ElementInterpreter code is extremely simple --
        it doesn't have to do anything to deallocate constants!
        (Since the memory for constants is actually allocated as a
        Python list, and Cython knows how to deallocate Python lists.)

        Finally we get to the __call__ method.  We grab the arguments
        passed by the caller, stuff them in our pre-allocated
        argument array, and then call the C interpreter.

        We optionally adjust the return value of the interpreter
        (currently only the RDF/float interpreter performs this step;
        this is the only place where domain=RDF differs than
        domain=float)::

            sage: print(rdf_wrapper)
            # ...
                def __call__(self, *args):
                    if self._n_args != len(args): raise ValueError
                    cdef double* c_args = self._args
                    cdef int i
                    for i from 0 <= i < len(args):
                        self._args[i] = args[i]
                    return self._domain(interp_rdf(c_args
                        , self._constants
                        , self._py_constants
                        , self._stack
                        , self._code
                        ))
            ...

        In Python-object based interpreters, the call to the C
        interpreter has to be a little more complicated.  We don't
        want to hold on to Python objects from an old computation by
        leaving them referenced from the stack.  In normal operation,
        the C interpreter clears out the stack as it runs, leaving the
        stack totally clear when the interpreter finishes.  However,
        this doesn't happen if the C interpreter raises an exception.
        In that case, we have to clear out any remnants from the stack
        in the wrapper::

            sage: print(el_wrapper)
            # ...
                    try:
                        return interp_el((<PyListObject*>mapped_args).ob_item
                            , self._constants
                            , self._stack
                            , <PyObject*>self._domain
                            , self._code
                            )
                    except BaseException:
                        for i in range(self._n_stack):
                            Py_CLEAR(self._stack[i])
                        raise
            ...

        Finally, we define a cdef call_c method, for quickly calling
        this object from Cython.  (The method is omitted from
        Python-object based interpreters.)::

            sage: print(rdf_wrapper)
            # ...
                cdef bint call_c(self,
                                 double* args,
                                 double* result) except 0:
                    result[0] = interp_rdf(args
                        , self._constants
                        , self._py_constants
                        , self._stack
                        , self._code
                        )
                    return 1
            ...

        The method for the RR interpreter is slightly different, because
        the interpreter takes a pointer to a result location instead of
        returning the value::

            sage: print(rr_wrapper)
            # ...
                cdef bint call_c(self,
                                 mpfr_t* args,
                                 mpfr_t result) except 0:
                    interp_rr(args
                        , result
                        , self._constants
                        , self._py_constants
                        , self._stack
                        , self._code
                        , <PyObject*>self._domain
                        )
                    return 1
            ...

        That's it for the wrapper class.  The only thing remaining is
        the interpreter metadata.  This is the information necessary
        for the code generator to map instruction names to opcodes; it
        also gives information about stack usage, etc.  This is fully
        documented at InterpreterMetadata; for now, we'll just show
        what it looks like.

        Currently, there are three parts to the metadata; the first maps
        instruction names to instruction descriptions.  The second one
        maps opcodes to instruction descriptions.  Note that we don't
        use InstrSpec objects here; instead, we use CompilerInstrSpec
        objects, which are much simpler and contain only the information
        we'll need at runtime.  The third part says what range the
        ipow instruction is defined over.

        First the part that maps instruction names to
        (CompilerInstrSpec, opcode) pairs::

            sage: print(rdf_wrapper)
            # ...
            from sage.ext.fast_callable import CompilerInstrSpec, InterpreterMetadata
            metadata = InterpreterMetadata(by_opname={
            ...
              'return':
              (CompilerInstrSpec(1, 0, []), 2),
              'py_call':
              (CompilerInstrSpec(0, 1, ['py_constants', 'n_inputs']), 3),
              'pow':
              (CompilerInstrSpec(2, 1, []), 4),
              'add':
              (CompilerInstrSpec(2, 1, []), 5),
            ...
             }, ...)

        There's also a table that maps opcodes to (instruction name,
        CompilerInstrSpec) pairs::

            sage: print(rdf_wrapper)
            # ...
            metadata = InterpreterMetadata(...,  by_opcode=[
            ...
              ('return',
               CompilerInstrSpec(1, 0, [])),
              ('py_call',
               CompilerInstrSpec(0, 1, ['py_constants', 'n_inputs'])),
              ('pow',
               CompilerInstrSpec(2, 1, [])),
              ('add',
               CompilerInstrSpec(2, 1, [])),
            ...
             ], ...)

        And then the ipow range::

            sage: print(rdf_wrapper)
            # ...
            metadata = InterpreterMetadata(...,
              ipow_range=(-2147483648, 2147483647))

        And that's it for the wrapper.
        """

        buff = StringIO()
        self.write_wrapper(buff.write)
        return buff.getvalue()

    def get_pxd(self):
        r"""
        Return the code for the Cython .pxd file.

        EXAMPLES:

        First we get the InterpreterSpec for several interpreters::

            sage: from sage_setup.autogen.interpreters import *
            sage: rdf_spec = RDFInterpreter()
            sage: rr_spec = RRInterpreter()
            sage: el_spec = ElementInterpreter()

        Then we get the corresponding .pxd::

            sage: rdf_pxd = InterpreterGenerator(rdf_spec).get_pxd()
            sage: rr_pxd = InterpreterGenerator(rr_spec).get_pxd()
            sage: el_pxd = InterpreterGenerator(el_spec).get_pxd()

        Now we can look through these pxd files.

        Each .pxd starts with a file header; this can be
        customized on a per-interpreter basis (some blank lines have been
        elided below)::

            sage: print(rdf_pxd)
            # Automatically generated by ...
            from cpython cimport PyObject
            from sage.ext.fast_callable cimport Wrapper
            ...
            sage: print(rr_pxd)
            # ...
            from sage.rings.real_mpfr cimport RealField_class, RealNumber
            from sage.libs.mpfr cimport *
            ...

        Next and last is the declaration of the wrapper class, which
        starts off with a list of member declarations::

            sage: print(rdf_pxd)
            # ...
            cdef class Wrapper_rdf(Wrapper):
                cdef int _n_args
                cdef double* _args
                cdef int _n_constants
                cdef double* _constants
                cdef object _list_py_constants
                cdef int _n_py_constants
                cdef PyObject** _py_constants
                cdef int _n_stack
                cdef double* _stack
                cdef int _n_code
                cdef int* _code
            ...

        Contrast the declaration of ``_stack`` here with the
        ElementInterpreter version.  To simplify our handling of
        reference counting and garbage collection, in a Python-object
        based interpreter, we allocate arrays as Python lists,
        and then pull the array out of the innards of the list::

            sage: print(el_pxd)
            # ...
                cdef object _list_stack
                cdef int _n_stack
                cdef PyObject** _stack
            ...

        Then, at the end of the wrapper class, we declare a cdef method
        for quickly calling the wrapper object from Cython.  (This method
        is omitted from Python-object based interpreters.)::

            sage: print(rdf_pxd)
            # ...
                cdef bint call_c(self,
                                 double* args,
                                 double* result) except 0
            sage: print(rr_pxd)
            # ...
                cdef bint call_c(self,
                                 mpfr_t* args,
                                 mpfr_t result) except 0

        """

        buff = StringIO()
        self.write_pxd(buff.write)
        return buff.getvalue()
