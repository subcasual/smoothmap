"""Resolving the names written in patch sets to ABC indices, and back.

    HubMap                    a class: its short name if unique, else package::Class
    HubMap.handleShowTimer    a member, via the class's own trait (so private names work too);
                              package::Class.member when the class needs its package
    HubMap.<constructor>      special methods; also <static-init>
    flash.utils::getTimer     a name in a package (public::name: the top-level public name)
    alpha                     a plain name: a class's short name, or an existing public name
    AS3::push                 a name in the AS3 namespace ("uri"::name: in any namespace, by URI)
    untyped alpha             alpha looked up through the class's open namespaces, as compiled code
                              does when it can't resolve a name at compile time (a Multiname)
    <runtime-name>            a name taken from the stack, looked up the same way (a[i])
    Vector.<flash.geom::Point>  a parameterized type

`untyped name` and `<runtime-name>` use the namespace set of the class whose code they're in: the one its
methods already use most for such lookups.
"""
from __future__ import annotations

from collections import Counter
from typing import Iterator

from .abcfile import (ABC, MULTINAME, MULTINAME_A, MULTINAME_L, MULTINAME_LA, NAMESPACE, PACKAGE_INTERNAL_NS,
                      PACKAGE_NS, PRIVATE_NS, PROTECTED_NS, QNAME, STATIC_PROTECTED_NS, TRAIT_GETTER, TRAIT_METHOD,
                      TRAIT_SETTER, TYPENAME, Trait)
from .errors import PatchError
from .instructions import CALL_OPS, NAME_OPS, Instruction, decode

VISIBILITY_NS_KINDS = {"public": PACKAGE_NS, "internal": PACKAGE_INTERNAL_NS, "protected": PROTECTED_NS,
                       "private": PRIVATE_NS}
AS3_NAMESPACE = "http://adobe.com/AS3/2006/builtin"
UNTYPED = "untyped "
RUNTIME_NAME = "<runtime-name>"


class Names:
    def __init__(self, abc: ABC) -> None:
        self.abc = abc
        self._method_classes: dict[int, int] | None = None     # method index -> class index
        self._open_sets: dict[int, int | None] = {}             # class index -> namespace set
        self._member_owners: dict[tuple, list[int]] | None = None   # trait name (content) -> classes
        self._classes_by_name: dict[str, list[int]] = {}         # short name -> class indices

    # ----------------------------------------------------------------------- classes

    def find_class(self, ref: str) -> int:
        """Class index for `Class` or `package::Class`."""
        abc = self.abc
        package, _, short = ref.rpartition("::")
        if not self._classes_by_name:
            for i in range(len(abc.instances)):
                self._classes_by_name.setdefault(abc.class_name(i), []).append(i)
        hits = [i for i in self._classes_by_name.get(short, []) if not package or abc.class_package(i) == package]
        if len(hits) > 1 and not package:
            # Flash timelines can define a symbol class with the same name in the top-level package;
            # prefer the one real (packaged) class if there is exactly one.
            packaged = [i for i in hits if abc.class_package(i)]
            hits = packaged if len(packaged) == 1 else hits
        if len(hits) != 1:
            hint = "; qualify it as package::Class" if hits else ""
            raise PatchError(f"class '{ref}': {len(hits)} matches{hint}")
        return hits[0]

    def class_method_indices(self, class_index: int) -> set[int]:
        """Every method of a class: constructor, static initializer, methods and accessors."""
        instance, cls = self.abc.instances[class_index], self.abc.classes[class_index]
        members = {t.method for t in instance.traits + cls.traits if t.method is not None}
        return {instance.constructor, cls.static_init} | members

    def class_of_method(self, method_index: int) -> int | None:
        """The class a method belongs to, or None (script initializers, closures)."""
        if self._method_classes is None:
            self._method_classes = {method: class_index for class_index in range(len(self.abc.instances))
                                    for method in self.class_method_indices(class_index)}
        return self._method_classes.get(method_index)

    def open_namespaces(self, class_index: int) -> int | None:
        """The namespace set the class's code looks up unresolved names in (Multiname operands): the
        one its methods use most (ties: the lowest index). None if its code has no such lookups."""
        if class_index not in self._open_sets:
            abc, uses = self.abc, Counter()
            for method in sorted(self.class_method_indices(class_index)):
                body = abc.methods[method].body
                for item in decode(body.code) if body else []:
                    if isinstance(item, Instruction) and (item.op in NAME_OPS or item.op in CALL_OPS):
                        kind, *fields = abc.multinames[item.args[0]] or (None,)
                        if kind in (MULTINAME, MULTINAME_A, MULTINAME_L, MULTINAME_LA):
                            uses[fields[-1]] += 1
            self._open_sets[class_index] = max(uses, key=lambda s: (uses[s], -s)) if uses else None
        return self._open_sets[class_index]

    # ----------------------------------------------------------------------- members

    def trait(self, class_index: int, member: str) -> tuple[Trait, bool]:
        """(trait, is static) of a class member, instance or static. A getter and setter pair
        counts as one member (the getter's trait is returned)."""
        abc = self.abc
        found = [(t, static)
                 for static, traits in ((False, abc.instances[class_index].traits), (True, abc.classes[class_index].traits))
                 for t in traits if abc.name_of(t.name) == member]
        if len(found) == 2 and found[0][0].name == found[1][0].name and found[0][1] == found[1][1]:
            found = found[:1]
        if len(found) != 1:
            raise PatchError(f"member '{member}' of {abc.class_name(class_index)}: {len(found)} matches")
        return found[0]

    def method(self, ref: str) -> int:
        """Method index for Class.method, Class.<constructor> or Class.<static-init>. The member can
        say which of a getter/setter pair (Class.get x, Class.set x), or that it's static
        (Class.static x, Class.static get x), as the tools name methods."""
        class_index, member = self._split_member(ref)
        if member == "<constructor>":
            return self.abc.instances[class_index].constructor
        if member == "<static-init>":
            return self.abc.classes[class_index].static_init
        words = member.split()
        static = True if len(words) > 1 and words[0] == "static" else None
        accessor = {"get": TRAIT_GETTER, "set": TRAIT_SETTER}.get(words[-2]) if len(words) > 1 else None
        if static is None and accessor is None:
            trait, _ = self.trait(class_index, member)
        else:
            abc = self.abc
            found = [t for is_static, traits in ((False, abc.instances[class_index].traits),
                                                 (True, abc.classes[class_index].traits))
                     for t in traits if abc.name_of(t.name) == words[-1] and static in (None, is_static)
                     and t.base_kind == (accessor if accessor is not None else TRAIT_METHOD)]
            if len(found) != 1:
                raise PatchError(f"'{ref}': {len(found)} matches")
            trait = found[0]
        if trait.method is None:
            raise PatchError(f"'{ref}' is not a method")
        return trait.method

    def member_name(self, ref: str) -> int:
        """Multiname index for Class.member. Public members use the file's first matching public
        name (public names are shared by every class); others use the class's own trait name."""
        abc = self.abc
        class_index, member = self._split_member(ref)
        trait, _ = self.trait(class_index, member)
        namespace = abc.namespaces[abc.multinames[trait.name][1]]
        if namespace.kind == PACKAGE_NS and abc.string(namespace.name) == "":
            return abc.find_qname("", member)
        return trait.name

    def _split_member(self, ref: str) -> tuple[int, str]:
        cls, _, member = ref.rpartition(".")
        if not cls:
            raise PatchError(f"'{ref}' is not Class.member")
        return self.find_class(cls), member

    # ----------------------------------------------------------------------- operands

    def operand(self, token: str, context: int | None = None) -> int:
        """Multiname index for a name operand written in assembly (see the module docstring).
        context: the class whose code the operand is in (for untyped names and <runtime-name>)."""
        abc = self.abc
        if token == RUNTIME_NAME:
            return abc.add_multiname((MULTINAME_L, self._open_set(context, token)))
        if token.startswith(UNTYPED):
            name = token[len(UNTYPED):].strip()
            return abc.add_multiname((MULTINAME, abc.add_string(name), self._open_set(context, token)))
        if token.endswith(">") and ".<" in token:
            base, _, parameters = token[:-1].partition(".<")
            return abc.add_multiname((TYPENAME, self.operand(base, context),
                                      [self.operand(p.strip(), context) for p in parameters.split(",")]))
        if "::" in token:
            qualifier, _, short = token.rpartition("::")
            if "." in short:
                return self.member_name(token)                  # package::Class.member
            if qualifier == "AS3" or qualifier[:1] == qualifier[-1:] == '"':
                namespace, kind = (AS3_NAMESPACE if qualifier == "AS3" else qualifier[1:-1]), NAMESPACE
            else:
                namespace, kind = ("" if qualifier == "public" else qualifier), PACKAGE_NS
            try:
                return abc.find_qname(namespace, short, kind)
            except LookupError:
                raise PatchError(f"no name '{token}' in the file")
        if "." in token:
            return self.member_name(token)
        try:
            return abc.find_qname(abc.class_package(self.find_class(token)), token)
        except (PatchError, LookupError):
            pass
        try:
            return abc.find_qname("", token)
        except LookupError:
            raise PatchError(f"no public name or class '{token}' in the file "
                             f"(private members are written Class.member)")

    def add_name(self, token: str) -> int:
        """Multiname index for a fully qualified name (`public::Name`, `package::Name`, `AS3::name`,
        `"uri"::name`), adding it (and its namespace) if the file doesn't have it yet."""
        qualifier, separator, short = token.rpartition("::")
        if not separator or not short or "." in short:
            raise PatchError(f"'{token}': new names are written qualified: public::Name, package::Name, "
                             f"AS3::name or \"uri\"::name")
        if qualifier == "AS3" or qualifier[:1] == qualifier[-1:] == '"':
            namespace, kind = (AS3_NAMESPACE if qualifier == "AS3" else qualifier[1:-1]), NAMESPACE
        else:
            namespace, kind = ("" if qualifier == "public" else qualifier), PACKAGE_NS
        try:
            return self.abc.find_qname(namespace, short, kind)
        except LookupError:
            return self.abc.add_multiname((QNAME, self.abc.add_namespace(namespace, kind), self.abc.add_string(short)))

    def _open_set(self, context: int | None, token: str) -> int:
        open_set = self.open_namespaces(context) if context is not None else None
        if open_set is None:
            raise PatchError(f"'{token}' needs a class whose code already looks names up this way")
        return open_set

    def text(self, multiname: int, context: int | None = None) -> str | None:
        """The operand text that resolves back to exactly this multiname (as `operand` reads it, in
        the same file and context), or None if the syntax can't express it."""
        for candidate in self._candidates(multiname, context):
            try:
                resolved = self.operand(candidate, context)
            except PatchError:
                continue
            if self.abc.multinames[resolved] == self.abc.multinames[multiname]:
                return candidate
        return None

    def _candidates(self, multiname: int, context: int | None) -> Iterator[str]:
        """Ways to write a multiname, most readable first; `text` keeps the first that resolves back."""
        abc = self.abc
        kind, *fields = abc.multinames[multiname] or (None,)
        if kind == QNAME:
            namespace, name = abc.namespaces[fields[0]], abc.string(fields[1])
            if namespace.kind == PACKAGE_NS:
                yield name
                yield f"{abc.string(namespace.name) or 'public'}::{name}"
            for owner in self._owners(abc.multinames[multiname]):
                yield f"{abc.class_name(owner)}.{name}"
                yield f"{abc.class_package(owner)}::{abc.class_name(owner)}.{name}"
            if namespace.kind == NAMESPACE:
                uri = abc.string(namespace.name)
                yield f"AS3::{name}" if uri == AS3_NAMESPACE else f'"{uri}"::{name}'
        elif kind in (MULTINAME, MULTINAME_L) and context is not None and fields[-1] == self.open_namespaces(context):
            yield UNTYPED + abc.string(fields[0]) if kind == MULTINAME else RUNTIME_NAME
        elif kind == TYPENAME:
            parts = [self.text(fields[0], context)] + [self.text(p, context) for p in fields[1]]
            if None not in parts:
                yield f"{parts[0]}.<{', '.join(parts[1:])}>"

    def _owners(self, multiname: tuple) -> list[int]:
        """The classes (and interfaces) with a member trait named exactly this."""
        if self._member_owners is None:
            self._member_owners = {}
            for class_index, (instance, cls) in enumerate(zip(self.abc.instances, self.abc.classes)):
                for trait in instance.traits + cls.traits:
                    owners = self._member_owners.setdefault(self.abc.multinames[trait.name], [])
                    if class_index not in owners:
                        owners.append(class_index)
        return self._member_owners.get(multiname, [])

    def type_name(self, ref: str) -> int:
        """Multiname index for a type: `Class` (top level), `package::Class`, or a parameterized type
        (`__AS3__.vec::Vector.<int>`)."""
        if ref.endswith(">") and ".<" in ref:
            return self.operand(ref)
        package, _, short = ref.rpartition("::")
        try:
            return self.abc.find_qname(package, short)
        except LookupError:
            raise PatchError(f"type '{ref}' not found")

    # ----------------------------------------------------------------------- new members

    def namespace_for(self, class_index: int, visibility: str, static: bool) -> int:
        """The namespace a new member of the class gets for a visibility. Private and protected
        namespaces are per class, so they're taken from the class's existing members."""
        abc = self.abc
        instance = abc.instances[class_index]
        if visibility == "public":
            return abc.find_namespace("")
        if visibility == "internal":
            return abc.find_namespace(abc.class_package(class_index), PACKAGE_INTERNAL_NS)
        if visibility == "protected" and not static and instance.protected_ns:
            return instance.protected_ns
        kind = STATIC_PROTECTED_NS if (visibility == "protected" and static) else VISIBILITY_NS_KINDS.get(visibility)
        if kind is None:
            raise PatchError(f"visibility '{visibility}' must be private, protected, internal or public")
        member_namespaces = {abc.multinames[t.name][1] for t in instance.traits + abc.classes[class_index].traits}
        used = sorted(ns for ns in member_namespaces if abc.namespaces[ns].kind == kind)
        if len(used) != 1:
            raise PatchError(f"class {abc.class_name(class_index)}: can't tell its {visibility}"
                             f"{' static' if static else ''} namespace ({len(used)} candidates among its members)")
        return used[0]
