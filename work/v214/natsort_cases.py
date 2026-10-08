from natsort import natsorted, humansorted, realsorted, index_natsorted, order_by_index, natsort_keygen, ns, as_ascii, as_utf8, decoder, chain_functions

ITEMS = ["item10", "item9", "item1", "item2"]

def case_basic():
    return natsorted(ITEMS)

def case_reverse():
    return natsorted(ITEMS, reverse=True)

def case_float():
    return natsorted(["1.5", "1.10", "1.2"], alg=ns.FLOAT)

def case_signed():
    return natsorted(["a-5", "a+2", "a3", "a-1"], alg=ns.SIGNED)

def case_real():
    return realsorted(["-1.5", "2", "-3", "0.5"])

def case_ignorecase():
    return natsorted(["Banana", "apple", "banana", "Apple"], alg=ns.IGNORECASE)

def case_lowercasefirst():
    return natsorted(["Banana", "apple", "banana", "Apple"], alg=ns.LOWERCASEFIRST)

def case_groupletters():
    return natsorted(["Banana", "apple", "banana", "Apple"], alg=ns.GROUPLETTERS)

def case_path():
    return natsorted(["a/b10/c", "a/b2/c", "a/b2.txt", "a/b10.txt"], alg=ns.PATH)

def case_nan_default():
    return [str(x) for x in natsorted([3.0, float("nan"), 1.0])]

def case_index():
    return index_natsorted(ITEMS)

def case_order_by_index():
    return order_by_index(ITEMS, index_natsorted(ITEMS))

def case_keygen():
    return sorted(ITEMS, key=natsort_keygen())

def case_numafter():
    return natsorted(["a", "1", "b", "2"], alg=ns.NUMAFTER)

def case_humansorted():
    return humansorted(["b", "a2", "A1"])

def case_as_ascii():
    return as_ascii(b"abc")

def case_as_utf8():
    return as_utf8(b"\xc3\xa9")

def case_decoder():
    return decoder("utf8")(b"x")

def case_chain():
    return chain_functions([str.upper, str.strip])("  ab ")
