from gen3tl.lang import termbase as tb
from project_fixture import select_language

def setUpModule():
    select_language("ru")

try:
    import pymorphy3  # noqa: F401
    HAVE_MORPH = True
except ImportError:
    HAVE_MORPH = False

def table(sing: list[str], plur: list[str]) -> dict[str, str]:
    """A form table from the six singular and six plural cases."""
    out = {f"{c}.sing": f for c, f in zip(tb.CASES, sing)}
    out.update({f"{c}.plur": f for c, f in zip(tb.CASES, plur)})
    return out

SEEL = tb.Term("species:seel", "species", "Seel", "Сил", declension="manual",
               forms=table(["Сил", "Сила", "Силу", "Сила", "Силом", "Силе"],
                           ["Силы", "Силов", "Силам", "Силов", "Силами", "Силах"]))
