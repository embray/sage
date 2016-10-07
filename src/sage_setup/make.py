import glob
import os
import re
from collections import OrderedDict, defaultdict

from six import iterkeys, itervalues, iteritems, add_metaclass


_GLOB_RE = re.compile('([?*[]])')


class MakeError(Exception):
    pass


class rule(object):
    def __new__(cls, targets, prerequisites=(), recipe=None):
        if recipe is None:
            cls = unbound_rule

        return super(rule, cls).__new__(cls)

    def __init__(self, targets, prerequisites=(), recipe=None):
        if isinstance(targets, str):
            targets = (targets,)

        if isinstance(prerequisites, str):
            prerequisites = (prerequisites,)

        self.targets = targets
        self.prerequisites = prerequisites
        self.recipe = recipe

    def __call__(self, makefile, target, prerequisites):
        # TODO: Rather than pass makefile in explicitly it would be nice
        # if all recipe functions were properly made into bound methods
        # (or static/classmethods as the case may be)
        return self.recipe(makefile, target, prerequisites)


class unbound_rule(rule):
    def __call__(self, recipe):
        """
        Also allows `rule` to be used a decorator to set its recipe.

        Returns a new *bound* `rule` with its ``recipe`` attribute set to the
        decorated method.
        """

        return rule(self.targets, self.prerequisites, recipe)


class _MakefileMeta(type):
    def __new__(metacls, name, bases, namespace):
        metacls._process_rules(name, namespace)
        metacls._detect_cycles(namespace['dependencies'])
        return super(_MakefileMeta, metacls).__new__(metacls, name, bases,
                                                     namespace)

    @staticmethod
    def _process_rules(name, namespace):
        """
        Collect up all rules in the class and make a directed graph, with
        nodes representing targets and edges ``A -> B`` representing the
        relationship "A depends on B".

        Also keep an ordered dict mapping targets to their associated rules.
        globs in targets and dependencies are expanded at this point as well.

        Updates the namespace in-place.
        """

        targets = OrderedDict()
        dependencies = defaultdict(list)

        for value in itervalues(namespace):
            if not isinstance(value, rule):
                continue

            rule_targets = _expand_files(value.targets)
            rule_prereqs = _expand_files(value.prerequisites)

            for target in rule_targets:
                if target not in targets:
                    targets[target] = [value]
                else:
                    # A single target may have multiple rules for it
                    # (though it may have only one non-unbound_rule; see
                    # below)
                    targets[target].append(value)

                dependencies[target].extend(rule_prereqs)

        # First, pass through dependencies and eliminate any duplicates
        # in each target's dependency list
        for target, deps in iteritems(dependencies):
            dependencies[target] = tuple(_unique(deps))

        # Pass through each target's rules, removing now any unbound_rules
        # If more than one bound rule is remaining this is an error
        for target, rules in iteritems(targets):
            rules = [r for r in rules if not isinstance(r, unbound_rule)]
            if not rules:
                raise MakeError('no rule to make target {0} in {1}'.format(
                    target, name))

            if len(rules) > 1:
                rule_names = ', '.join(r.__name__ for r in rules)
                raise MakeError('more than one recipe ({0}) for target {1} '
                                'in {2}; each target may have only one '
                                'recipe'.format(rule_names, target, name))

            targets[target] = rules[0]

        # TODO: Because these have special meaning for the Makefile, consider
        # raising an error if the user accidentally defines these attributes in
        # the body of their Makefile
        namespace.update(targets=targets, dependencies=dependencies)

    @staticmethod
    def _detect_cycles(dependencies):
        visited = set()

        # in lieu of an "ordered set"
        in_stack = set()
        stack = []

        def visit_node(node, prereqs):
            # Returns True if the node is part of a cycle
            visited.add(node)
            stack.append(node)
            in_stack.add(node)

            for n in prereqs:
                # Targets are allowed to have dependencies for which no target
                # is defined; that dependency just better exist at runtime,
                # otherwise we treat them as leaf nodes
                if n not in dependencies:
                    continue

                if n not in visited and visit_node(n, dependencies[n]):
                    return True
                elif n in in_stack:
                    return True

            stack.pop()
            in_stack.remove(node)
            return False

        for node, prereqs in iteritems(dependencies):
            if visit_node(node, prereqs):
                cycle = ' -> '.join(stack + [node])
                raise MakeError('cycle detected in targets: {0}'.format(cycle))


@add_metaclass(_MakefileMeta)
class Makefile(object):
    def __init__(self, target=None):
        if target is None:
            # The first target in the file is the default
            target = iterkeys(self.targets).next()

        # TODO: There are many attributes and/or methods to add to the Makefile
        # class to make introspection better.  For example, there should be
        # a log system whereby each rule can easily log output and errors to
        # be captured by the Makefile instance.  There should also be options
        # whether to raise exceptions immediately or just capture and log them,
        # as well as a flag, I suppose, for whether or not the build was
        # successful
        self._make(target)

    def _make(self, target):
        dependencies = self.dependencies
        targets = self.targets

        def visit_node(node, parent):
            if node not in dependencies:
                # There are no targets for this node so it had better
                # be a file that exists
                if not os.path.exists(node):
                    if parent is None:
                        msg = 'no rule to make target {0}'.format(node)
                    else:
                        msg = ('no rule to make prerequisite {0} of target '
                               '{1}'.format(node, parent))

                    raise MakeError(msg)

                return

            for prereq in dependencies[node]:
                visit_node(prereq, node)

            # If the current target does not exist, it must be built no matter
            # what
            node_rule = targets[node]

            if not os.path.exists(node):
                # TODO: handle errors from running the rule
                node_rule(self, node, dependencies[node])
                return

            # TODO: There should also be error handling at this point to make
            # sure that all of the current target's prerequisites exist.  If
            # their rules were implemented properly then they should, but
            # obvious an error in the makefile itself could lead to
            # inconsistencies

            # Now loop over the prerequisites again, and if any are newer than
            # the current target, rebuild the current target
            modtime = os.stat(node).st_mtime
            newer_prereqs = [prereq for prereq in dependencies[node]
                             if os.stat(prereq).st_mtime > modtime]
            if newer_prereqs:
                node_rule(self, node, newer_prereqs)

        visit_node(target, None)


def _expand_files(files):
    """
    Given a list of filenames, some of which may contain wildcard patterns,
    return a new list where each wildcard is expanded in-place.
    """

    new_files = []
    for filename in files:
        if _GLOB_RE.search(filename):
            new_files.extend(glob.glob(filename))
        else:
            new_files.append(filename)

    return new_files


def _unique(items):
    def seen(item, _seen=set()):
        if item in _seen:
            return True

        _seen.add(item)
        return False

    return [item for item in items if not seen(item)]
