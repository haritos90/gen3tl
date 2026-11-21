from gen3tl.verify.savefile import EOS
from project_fixture import select_language

def setUpModule():
    select_language("ru")

def padded(name: bytes, size: int) -> bytes:
    return (name + bytes([EOS]) * size)[:size]
