"""Schema model: node classes with typed properties, edge types with named roles."""

from __future__ import annotations

from dataclasses import dataclass, field

GRANULARITY_STATES = {"blob", "composite", "atom"}


@dataclass
class ClassDef:
    name: str
    props: dict[str, str]  # prop name -> type name: str|int|float|bool
    base: str | None = None
    allowed_states: set[str] = field(default_factory=lambda: set(GRANULARITY_STATES))
    status: str = "stable"  # stable | provisional | deprecated
    quota: int | None = None
    dedup_threshold: float | None = None


@dataclass
class EdgeDef:
    name: str
    roles: dict[str, str]  # role name -> class name, exactly two roles
    props: dict[str, str] = field(default_factory=dict)  # edge's own props

    def other_role(self, role: str) -> str:
        for r in self.roles:
            if r != role:
                return r
        raise KeyError(role)


@dataclass
class Schema:
    classes: dict[str, ClassDef] = field(default_factory=dict)
    edges: dict[str, EdgeDef] = field(default_factory=dict)

    def __post_init__(self):
        b = self.classes
        b.setdefault("entity", ClassDef("entity", {"name": "str"}))
        b.setdefault("piece", ClassDef("piece", {}))
        b.setdefault(
            "document",
            ClassDef(
                "document",
                {"title": "str", "mime": "str", "pages": "int", "sha256": "str"},
            ),
        )
        b.setdefault(
            "chunk",
            ClassDef(
                "chunk", {"text": "str", "page": "int", "ord": "int"}, base="piece"
            ),
        )
        b.setdefault(
            "media",
            ClassDef(
                "media",
                {"caption": "str", "format": "str", "page": "int"},
                base="piece",
            ),
        )
        self.edges.setdefault(
            "part_of", EdgeDef("part_of", {"piece": "piece", "whole": "document"})
        )

    def is_subclass(self, cls: str, base: str) -> bool:
        """True when cls is base or derives (transitively) from base."""
        cdef = self.classes.get(cls)
        while cdef is not None:
            if cdef.name == base:
                return True
            cdef = self.classes.get(cdef.base) if cdef.base else None
        return False

    def all_props(self, cls: str) -> dict[str, str]:
        """Props of a class including inherited base-class props."""
        out: dict[str, str] = {}
        chain = []
        cdef = self.classes.get(cls)
        while cdef is not None:
            chain.append(cdef)
            cdef = self.classes.get(cdef.base) if cdef.base else None
        for cdef in reversed(chain):
            out.update(cdef.props)
        return out

    # ---- evolution: shared by the verifier (staging), the writes, and replay

    def own_prop(self, cls: str, prop: str) -> None:
        """Raise unless `prop` is declared on `cls` itself, naming the declarer otherwise."""
        cdef = self.classes[cls]
        if prop in cdef.props:
            return
        base = cdef.base
        while base is not None:
            if prop in self.classes[base].props:
                raise ValueError(f'property "{prop}" is declared on base class {base}; change it there')
            base = self.classes[base].base
        raise KeyError(prop)

    def rename_class(self, old: str, new: str) -> None:
        cdef = self.classes.pop(old)
        cdef.name = new
        self.classes[new] = cdef
        for other in self.classes.values():
            if other.base == old:
                other.base = new
        for edef in self.edges.values():
            for role, cls in edef.roles.items():
                if cls == old:
                    edef.roles[role] = new

    def rename_prop(self, cls: str, old: str, new: str) -> None:
        self.own_prop(cls, old)
        cdef = self.classes[cls]
        cdef.props = {(new if k == old else k): v for k, v in cdef.props.items()}

    def alter_add(self, cls: str, decls: dict[str, str]) -> None:
        self.classes[cls].props.update(decls)

    def alter_drop(self, cls: str, names: list[str]) -> None:
        for name in names:
            self.own_prop(cls, name)
            del self.classes[cls].props[name]

    def alter_retype(self, cls: str, decls: dict[str, str]) -> None:
        for name, typ in decls.items():
            self.own_prop(cls, name)
            self.classes[cls].props[name] = typ

    def drop_class(self, name: str) -> list[str]:
        """Remove the class and every edge type naming it; returns the edge names."""
        del self.classes[name]
        gone = [e for e, edef in self.edges.items() if name in edef.roles.values()]
        for e in gone:
            del self.edges[e]
        return gone

    def drop_edge(self, name: str) -> None:
        del self.edges[name]

    @staticmethod
    def supply_chain() -> Schema:
        """The running-example schema from the design docs."""
        s = Schema()
        s.classes["product"] = ClassDef(
            "product", {"name": "str", "launch_year": "int"}
        )
        s.classes["part"] = ClassDef("part", {"name": "str", "unit_cost": "float"})
        s.classes["supplier"] = ClassDef("supplier", {"name": "str", "country": "str"})
        s.classes["table_blob"] = ClassDef(
            "table_blob",
            {"title": "str", "payload": "str"},
            base="piece",
            allowed_states={"blob", "composite"},
        )
        s.edges["uses"] = EdgeDef("uses", {"whole": "product", "component": "part"})
        s.edges["supplied_by"] = EdgeDef(
            "supplied_by",
            {"item": "part", "source": "supplier"},
            # a supply relationship has its own dates: the question "who
            # supplies this now" is about the edge, not either endpoint
            {"start_year": "int", "contract_end": "int"},
        )
        return s

    def render(self, store=None) -> str:
        """Explainer-format schema listing.

        With a store, only classes that hold data are listed, and only
        edges whose two ends are both listed. An agent choosing what to
        query should see the schema that can answer something; empty
        classes are prompt tokens on every turn and an invitation to
        traverse somewhere nothing lives. Without a store nothing is
        hidden, since a write workflow needs to see classes it has not
        populated yet.
        """
        populated = None
        if store is not None:
            live_by_class = getattr(store, "live_by_class", None)
            by_class = getattr(store, "by_class", {})
            if live_by_class is not None:
                populated = {cls for cls in by_class if live_by_class(cls)}
            else:
                populated = {cls for cls, ids in by_class.items() if ids}
        lines = ["classes:"]
        for c in self.classes.values():
            if populated is not None and c.name not in populated:
                continue
            props = ", ".join(c.props)
            suffix = ""
            if c.status == "provisional":
                suffix = f"  (provisional, quota {c.quota})"
            base = f" from {c.base}" if c.base else ""
            lines.append(f"  {c.name}{{{props}}}{base}{suffix}")
        lines.append("edges:")
        for e in self.edges.values():
            if populated is not None and not all(
                cls in populated for cls in e.roles.values()
            ):
                continue
            roles = ", ".join(f"{r}: {cls}" for r, cls in e.roles.items())
            # An edge's own properties are only reachable through via.,
            # so they have to be visible here or nobody can ask about them.
            props = f" via{{{', '.join(e.props)}}}" if e.props else ""
            lines.append(f"  {e.name}({roles}){props}")
        return "\n".join(lines)
